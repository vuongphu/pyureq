use base64::Engine as _;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use std::collections::HashMap;
use std::sync::Mutex;
use std::time::{Duration, Instant};
use ureq::config::Config;
use ureq::tls::{RootCerts, TlsConfig, TlsProvider};
use ureq::{Agent, ResponseExt};

// ---------------------------------------------------------------------------
// VerifyMode — mirrors requests' `verify` parameter semantics
//
//   verify=True   → use system / OS trust store (default)
//   verify=False  → skip certificate verification entirely (dangerous)
//   verify="/path/to/ca-bundle.pem" → use the supplied PEM file as the CA
// ---------------------------------------------------------------------------

#[derive(Clone, Debug, PartialEq, Eq, Hash)]
enum VerifyMode {
    /// Verify using the OS / system trust store.
    System,
    /// Skip all certificate verification.
    None,
    /// Verify using the given PEM CA-bundle file.
    CaBundle(String),
}

impl VerifyMode {
    /// Convert a Python value (bool or str/Path) into a `VerifyMode`.
    fn from_pyany(obj: &pyo3::Bound<'_, pyo3::PyAny>) -> PyResult<Self> {
        // Try bool first (True / False)
        if let Ok(b) = obj.extract::<bool>() {
            return Ok(if b {
                VerifyMode::System
            } else {
                VerifyMode::None
            });
        }
        // Try string / os.PathLike
        if let Ok(s) = obj.extract::<String>() {
            return Ok(VerifyMode::CaBundle(s));
        }
        Err(pyo3::exceptions::PyTypeError::new_err(
            "verify must be a bool or a path string to a CA bundle",
        ))
    }
}

// ---------------------------------------------------------------------------
// RawResponse
// ---------------------------------------------------------------------------

#[pyclass(module = "pyureq._pyureq")]
pub struct RawResponse {
    status_code: u16,
    headers: HashMap<String, String>,
    body: Vec<u8>,
    url: String,
    elapsed_secs: f64,
    encoding: Option<String>,
}

#[pymethods]
impl RawResponse {
    #[getter]
    fn status_code(&self) -> u16 {
        self.status_code
    }

    #[getter]
    fn headers(&self) -> HashMap<String, String> {
        self.headers.clone()
    }

    #[getter]
    fn body<'py>(&self, py: Python<'py>) -> Bound<'py, PyBytes> {
        PyBytes::new(py, &self.body)
    }

    #[getter]
    fn url(&self) -> &str {
        &self.url
    }

    #[getter]
    fn elapsed_secs(&self) -> f64 {
        self.elapsed_secs
    }

    #[getter]
    fn encoding(&self) -> Option<&str> {
        self.encoding.as_deref()
    }
}

// ---------------------------------------------------------------------------
// RustClient (Session backing)
//
// Holds a long-lived `ureq::Agent`, which *is* the connection pool.  Reusing it
// across requests is what gives Session its keep-alive behaviour.
//
// A per-request `verify` that differs from the session default gets its *own*
// agent, cached by VerifyMode.  This matters for correctness: ureq keys its
// connection pool on (scheme, authority, proxy) only — TLS config is not part
// of the key — so sharing one agent across verify modes would let a connection
// established with verify=False be reused to serve a later verify=True request,
// silently skipping certificate validation.  Separate agents = separate pools.
// ---------------------------------------------------------------------------

#[pyclass(module = "pyureq._pyureq")]
pub struct RustClient {
    /// Agent for the session's default `verify` mode.
    agent: Agent,
    /// The `verify` mode `agent` was built with.
    verify: VerifyMode,
    /// Agents for per-request `verify` overrides, one pool per mode.
    overrides: Mutex<HashMap<VerifyMode, Agent>>,
    default_timeout: Option<f64>,
    /// Session-level "disable all proxies"; a request may also set this itself.
    default_no_proxy_all: bool,
}

#[pymethods]
impl RustClient {
    #[new]
    #[pyo3(signature = (verify=None, timeout=None, no_proxy_all=false))]
    fn new(
        verify: Option<&pyo3::Bound<'_, pyo3::PyAny>>,
        timeout: Option<f64>,
        no_proxy_all: bool,
    ) -> PyResult<Self> {
        let verify_mode = match verify {
            Some(v) => VerifyMode::from_pyany(v)?,
            None => VerifyMode::System,
        };
        let agent = build_agent(&verify_mode)?;
        Ok(RustClient {
            agent,
            verify: verify_mode,
            overrides: Mutex::new(HashMap::new()),
            default_timeout: timeout,
            default_no_proxy_all: no_proxy_all,
        })
    }

    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (method, url, headers=None, params=None, body=None, content_type=None, auth=None, timeout=None, verify=None, allow_redirects=true, cookies=None, no_proxy_all=false, proxy=None))]
    fn request(
        &self,
        py: Python<'_>,
        method: &str,
        url: &str,
        headers: Option<HashMap<String, String>>,
        params: Option<Vec<(String, String)>>,
        body: Option<&[u8]>,
        content_type: Option<&str>,
        auth: Option<(String, String)>,
        timeout: Option<f64>,
        verify: Option<&pyo3::Bound<'_, pyo3::PyAny>>,
        allow_redirects: bool,
        cookies: Option<HashMap<String, String>>,
        no_proxy_all: bool,
        proxy: Option<String>,
    ) -> PyResult<RawResponse> {
        let effective_timeout = timeout.or(self.default_timeout);
        let effective_no_proxy_all = no_proxy_all || self.default_no_proxy_all;
        // Pick the agent whose TLS config matches the requested `verify`.  A
        // differing mode gets its own agent (and so its own connection pool)
        // rather than a TLS override on the shared one — see the note on
        // RustClient about ureq's pool key not covering TLS config.
        let agent = match verify {
            Some(v) => {
                let m = VerifyMode::from_pyany(v)?;
                if m == self.verify {
                    self.agent.clone()
                } else {
                    let mut cache = self
                        .overrides
                        .lock()
                        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
                    match cache.get(&m) {
                        Some(a) => a.clone(),
                        Option::None => {
                            // Agent is Arc-backed, so cloning is cheap.
                            let a = build_agent(&m)?;
                            cache.insert(m, a.clone());
                            a
                        }
                    }
                }
            }
            Option::None => self.agent.clone(),
        };
        // Clone/copy all data needed before releasing the GIL.
        let method = method.to_string();
        let url = url.to_string();
        let body_owned: Option<Vec<u8>> = body.map(|b| b.to_vec());
        let content_type = content_type.map(|s| s.to_string());
        py.detach(|| {
            http_execute(
                &agent,
                &method,
                &url,
                headers,
                params,
                body_owned.as_deref(),
                content_type.as_deref(),
                auth,
                effective_timeout,
                allow_redirects,
                cookies,
                proxy.as_deref(),
                effective_no_proxy_all,
            )
        })
    }
}

// ---------------------------------------------------------------------------
// Module-level one-shot function
// ---------------------------------------------------------------------------

#[pyfunction]
#[allow(clippy::too_many_arguments)]
#[pyo3(signature = (method, url, headers=None, params=None, body=None, content_type=None, auth=None, timeout=None, verify=None, allow_redirects=true, cookies=None, no_proxy_all=false, proxy=None))]
fn http_request(
    py: Python<'_>,
    method: &str,
    url: &str,
    headers: Option<HashMap<String, String>>,
    params: Option<Vec<(String, String)>>,
    body: Option<&[u8]>,
    content_type: Option<&str>,
    auth: Option<(String, String)>,
    timeout: Option<f64>,
    verify: Option<&pyo3::Bound<'_, pyo3::PyAny>>,
    allow_redirects: bool,
    cookies: Option<HashMap<String, String>>,
    no_proxy_all: bool,
    proxy: Option<String>,
) -> PyResult<RawResponse> {
    let verify_mode = match verify {
        Some(v) => VerifyMode::from_pyany(v)?,
        None => VerifyMode::System,
    };
    // Clone/copy all borrowed data before releasing the GIL.
    let method = method.to_string();
    let url = url.to_string();
    let body_owned: Option<Vec<u8>> = body.map(|b| b.to_vec());
    let content_type = content_type.map(|s| s.to_string());
    let agent = build_agent(&verify_mode)?;
    py.detach(|| {
        http_execute(
            &agent,
            &method,
            &url,
            headers,
            params,
            body_owned.as_deref(),
            content_type.as_deref(),
            auth,
            timeout,
            allow_redirects,
            cookies,
            proxy.as_deref(),
            no_proxy_all,
        )
    })
}

// ---------------------------------------------------------------------------
// TLS / Agent construction
// ---------------------------------------------------------------------------

/// Build a `TlsConfig` for the given `VerifyMode`.
///
/// `root_certs` is always set explicitly: ureq's default is `RootCerts::WebPki`,
/// which *panics* under the native-tls provider.  We use native-tls so that the
/// OS trust store is consulted (Windows CryptoAPI/Schannel, macOS Security
/// framework, Linux OpenSSL's standard CA paths).
fn build_tls_config(verify: &VerifyMode) -> PyResult<TlsConfig> {
    let builder = TlsConfig::builder().provider(TlsProvider::NativeTls);

    let tls = match verify {
        VerifyMode::None => {
            // Disable all cert + hostname checks.
            builder.disable_verification(true).build()
        }
        VerifyMode::System => {
            // PlatformVerifier == native-tls' built-in (OS) roots.
            builder.root_certs(RootCerts::PlatformVerifier).build()
        }
        VerifyMode::CaBundle(path) => {
            // Load the PEM file supplied by the caller (same as requests' verify="/path").
            let pem_bytes = std::fs::read(path).map_err(|e| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "Cannot read CA bundle '{}': {}",
                    path, e
                ))
            })?;
            // A CA bundle usually holds several certificates, so parse every
            // item: `Certificate::from_pem` would return only the first.
            let mut certs = Vec::new();
            for item in ureq::tls::parse_pem(&pem_bytes) {
                match item {
                    Ok(ureq::tls::PemItem::Certificate(c)) => certs.push(c),
                    // Ignore private keys and anything else in the bundle.
                    Ok(_) => {}
                    Err(e) => {
                        return Err(pyo3::exceptions::PyValueError::new_err(format!(
                            "Invalid CA bundle '{}': {}",
                            path, e
                        )));
                    }
                }
            }
            if certs.is_empty() {
                return Err(pyo3::exceptions::PyValueError::new_err(format!(
                    "No certificates found in CA bundle '{}'",
                    path
                )));
            }
            builder
                .root_certs(RootCerts::new_with_certs(&certs))
                .build()
        }
    };
    Ok(tls)
}

/// Build an `Agent` (and therefore a connection pool) for a `VerifyMode`.
///
/// Proxy selection is decided per request (see `http_execute`), so nothing
/// proxy-related is pinned here.
fn build_agent(verify: &VerifyMode) -> PyResult<Agent> {
    Ok(Config::builder()
        .tls_config(build_tls_config(verify)?)
        // requests does not raise on 4xx/5xx; callers use raise_for_status().
        .http_status_as_error(false)
        .build()
        .new_agent())
}

// ---------------------------------------------------------------------------
// Shared implementation (ureq — pure blocking, no tokio)
// ---------------------------------------------------------------------------

fn url_with_params(base: &str, params: &Option<Vec<(String, String)>>) -> String {
    let Some(p) = params else {
        return base.to_string();
    };
    if p.is_empty() {
        return base.to_string();
    }
    let sep = if base.contains('?') { '&' } else { '?' };
    let qs: Vec<String> = p
        .iter()
        .map(|(k, v)| format!("{}={}", pct(k), pct(v)))
        .collect();
    format!("{}{}{}", base, sep, qs.join("&"))
}

fn pct(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(b as char)
            }
            _ => out.push_str(&format!("%{:02X}", b)),
        }
    }
    out
}

/// Translate a `ureq::Error` into the Python exception the wrapper expects.
///
/// ureq 3 has a dedicated `Error::Timeout` variant, so timeouts are detected
/// structurally rather than by matching on OS error strings (which differ per
/// platform — Windows reports WSAETIMEDOUT without the words "timed out").
fn map_ureq_error(e: ureq::Error) -> PyErr {
    use ureq::Error;
    let msg = e.to_string();
    match e {
        Error::Timeout(_) => pyo3::exceptions::PyTimeoutError::new_err(msg),
        Error::HostNotFound | Error::ConnectionFailed | Error::Io(_) => {
            pyo3::exceptions::PyConnectionError::new_err(msg)
        }
        Error::TooManyRedirects => {
            pyo3::exceptions::PyRuntimeError::new_err(format!("TooManyRedirects: {}", msg))
        }
        Error::Tls(_) | Error::NativeTls(_) | Error::Pem(_) | Error::Der(_) => {
            pyo3::exceptions::PyRuntimeError::new_err(format!("SSLError: {}", msg))
        }
        Error::BadUri(_) | Error::Http(_) | Error::RequireHttpsOnly(_) => {
            pyo3::exceptions::PyValueError::new_err(msg)
        }
        Error::InvalidProxyUrl | Error::ConnectProxyFailed(_) => {
            pyo3::exceptions::PyRuntimeError::new_err(format!("ProxyError: {}", msg))
        }
        Error::Decompress(..) => {
            pyo3::exceptions::PyRuntimeError::new_err(format!("ContentDecodingError: {}", msg))
        }
        // Error is #[non_exhaustive], so a wildcard arm is required.
        _ => pyo3::exceptions::PyRuntimeError::new_err(msg),
    }
}

#[allow(clippy::too_many_arguments)]
fn http_execute(
    agent: &Agent,
    method: &str,
    url: &str,
    headers: Option<HashMap<String, String>>,
    params: Option<Vec<(String, String)>>,
    body: Option<&[u8]>,
    content_type: Option<&str>,
    auth: Option<(String, String)>,
    timeout: Option<f64>,
    allow_redirects: bool,
    cookies: Option<HashMap<String, String>>,
    proxy: Option<&str>,
    no_proxy_all: bool,
) -> PyResult<RawResponse> {
    let full_url = url_with_params(url, &params);

    let mut builder = ureq::http::Request::builder()
        .method(method.to_uppercase().as_str())
        .uri(&full_url);

    // Headers
    if let Some(hdrs) = headers {
        for (k, v) in &hdrs {
            builder = builder.header(k, v);
        }
    }

    // Cookies
    if let Some(cks) = cookies {
        if !cks.is_empty() {
            let s: String = cks
                .iter()
                .map(|(k, v)| format!("{}={}", k, v))
                .collect::<Vec<_>>()
                .join("; ");
            builder = builder.header("Cookie", &s);
        }
    }

    // Basic auth
    if let Some((user, pass)) = auth {
        let creds = base64::engine::general_purpose::STANDARD.encode(format!("{}:{}", user, pass));
        builder = builder.header("Authorization", &format!("Basic {}", creds));
    }

    // Content-Type
    if let Some(ct) = content_type {
        builder = builder.header("Content-Type", ct);
    }

    let req = builder
        .body(body.unwrap_or(&[]))
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

    // Per-request configuration overrides.  These leave the agent (and its
    // connection pool) intact.
    let mut cfg = agent.configure_request(req);
    cfg = cfg.max_redirects(if allow_redirects { 30 } else { 0 });
    if let Some(t) = timeout {
        cfg = cfg.timeout_global(Some(Duration::from_secs_f64(t)));
    }
    if let Some(p) = proxy {
        let proxy_obj = ureq::Proxy::new(p)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        cfg = cfg.proxy(Some(proxy_obj));
    } else if no_proxy_all {
        // proxies={} / {"http": ""} — suppress ureq's env-var proxy autodetect.
        cfg = cfg.proxy(Option::None);
    }
    let req = cfg.build();

    let start = Instant::now();
    let mut resp = agent.run(req).map_err(map_ureq_error)?;
    let elapsed = start.elapsed().as_secs_f64();

    let status_code = resp.status().as_u16();
    let final_url = resp.get_uri().to_string();

    let mut headers_out: HashMap<String, String> = HashMap::new();
    for (name, value) in resp.headers().iter() {
        let val = String::from_utf8_lossy(value.as_bytes()).to_string();
        headers_out
            .entry(name.as_str().to_lowercase())
            .and_modify(|e| {
                e.push_str(", ");
                e.push_str(&val);
            })
            .or_insert(val);
    }

    let encoding = headers_out.get("content-type").and_then(|ct| {
        ct.split(';')
            .find(|s| s.trim().to_lowercase().starts_with("charset="))
            .map(|s| s.trim()["charset=".len()..].trim().to_string())
    });

    // ureq caps read_to_vec() at 10 MB by default; lift it so large bodies are
    // not silently truncated.
    let body_bytes = resp
        .body_mut()
        .with_config()
        .limit(u64::MAX)
        .read_to_vec()
        .map_err(map_ureq_error)?;

    Ok(RawResponse {
        status_code,
        headers: headers_out,
        body: body_bytes,
        url: final_url,
        elapsed_secs: elapsed,
        encoding,
    })
}

/// TCP connectivity probe for debugging.
#[pyfunction]
fn tcp_probe(host: &str, port: u16) -> PyResult<bool> {
    use std::net::TcpStream;
    Ok(TcpStream::connect((host, port)).is_ok())
}

// ---------------------------------------------------------------------------
// Module
// ---------------------------------------------------------------------------

// gil_used = false: the extension holds no Python state while doing I/O — every
// request detaches from the interpreter first — so it is free-threading safe.
#[pymodule(name = "_pyureq", gil_used = false)]
fn pyureq_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RawResponse>()?;
    m.add_class::<RustClient>()?;
    m.add_function(wrap_pyfunction!(http_request, m)?)?;
    m.add_function(wrap_pyfunction!(tcp_probe, m)?)?;
    Ok(())
}
