use base64::Engine as _;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use std::collections::HashMap;
use std::io::Read;
use std::time::{Duration, Instant};

// ---------------------------------------------------------------------------
// VerifyMode — mirrors requests' `verify` parameter semantics
//
//   verify=True   → use system / OS trust store (default)
//   verify=False  → skip certificate verification entirely (dangerous)
//   verify="/path/to/ca-bundle.pem" → use the supplied PEM file as the CA
// ---------------------------------------------------------------------------

#[derive(Clone, Debug)]
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
// ---------------------------------------------------------------------------

#[pyclass(module = "pyureq._pyureq")]
pub struct RustClient {
    /// `verify` semantics (mirrors requests):
    ///   - `true`  → verify using system/default CA store
    ///   - `false` → skip certificate verification entirely
    ///   - path string → load that PEM file as the CA bundle
    verify: VerifyMode,
    default_timeout: Option<f64>,
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
        let _ = no_proxy_all; // ureq handles NO_PROXY automatically
        let verify_mode = match verify {
            Some(v) => VerifyMode::from_pyany(v)?,
            None => VerifyMode::System,
        };
        Ok(RustClient {
            verify: verify_mode,
            default_timeout: timeout,
        })
    }

    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (method, url, headers=None, params=None, body=None, content_type=None, auth=None, timeout=None, allow_redirects=true, cookies=None, proxy=None))]
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
        allow_redirects: bool,
        cookies: Option<HashMap<String, String>>,
        proxy: Option<String>,
    ) -> PyResult<RawResponse> {
        let effective_timeout = timeout.or(self.default_timeout);
        // Clone/copy all data needed before releasing the GIL.
        let method = method.to_string();
        let url = url.to_string();
        let body_owned: Option<Vec<u8>> = body.map(|b| b.to_vec());
        let content_type = content_type.map(|s| s.to_string());
        let verify = self.verify.clone();
        py.allow_threads(|| {
            http_execute(
                &method,
                &url,
                headers,
                params,
                body_owned.as_deref(),
                content_type.as_deref(),
                auth,
                effective_timeout,
                &verify,
                allow_redirects,
                cookies,
                proxy.as_deref(),
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
    let _ = no_proxy_all; // ureq reads NO_PROXY from env automatically
    let verify_mode = match verify {
        Some(v) => VerifyMode::from_pyany(v)?,
        None => VerifyMode::System,
    };
    // Clone/copy all borrowed data before releasing the GIL.
    let method = method.to_string();
    let url = url.to_string();
    let body_owned: Option<Vec<u8>> = body.map(|b| b.to_vec());
    let content_type = content_type.map(|s| s.to_string());
    py.allow_threads(|| {
        http_execute(
            &method,
            &url,
            headers,
            params,
            body_owned.as_deref(),
            content_type.as_deref(),
            auth,
            timeout,
            &verify_mode,
            allow_redirects,
            cookies,
            proxy.as_deref(),
        )
    })
}

// ---------------------------------------------------------------------------
// Build a TlsConnector from a VerifyMode
// ---------------------------------------------------------------------------

fn build_tls_connector(verify: &VerifyMode) -> PyResult<native_tls::TlsConnector> {
    match verify {
        VerifyMode::None => {
            // Disable all cert + hostname checks.
            native_tls::TlsConnector::builder()
                .danger_accept_invalid_certs(true)
                .danger_accept_invalid_hostnames(true)
                .build()
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
        }
        VerifyMode::System => {
            // Explicitly build with the OS trust store.
            // On Windows this uses the Windows certificate store (CryptoAPI/Schannel).
            // On macOS it uses the Security framework.
            // On Linux it uses OpenSSL which probes the standard system CA paths
            // (/etc/ssl/certs, /etc/pki/tls/certs, etc.).
            native_tls::TlsConnector::builder().build().map_err(|e| {
                pyo3::exceptions::PyRuntimeError::new_err(format!(
                    "TLS init failed (system CA store): {}",
                    e
                ))
            })
        }
        VerifyMode::CaBundle(path) => {
            // Load the PEM file supplied by the caller (same as requests' verify="/path")
            let pem_bytes = std::fs::read(path).map_err(|e| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "Cannot read CA bundle '{}': {}",
                    path, e
                ))
            })?;
            let cert = native_tls::Certificate::from_pem(&pem_bytes).map_err(|e| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "Invalid CA bundle '{}': {}",
                    path, e
                ))
            })?;
            native_tls::TlsConnector::builder()
                .add_root_certificate(cert)
                .build()
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
        }
    }
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

#[allow(clippy::too_many_arguments)]
fn http_execute(
    method: &str,
    url: &str,
    headers: Option<HashMap<String, String>>,
    params: Option<Vec<(String, String)>>,
    body: Option<&[u8]>,
    content_type: Option<&str>,
    auth: Option<(String, String)>,
    timeout: Option<f64>,
    verify: &VerifyMode,
    allow_redirects: bool,
    cookies: Option<HashMap<String, String>>,
    proxy: Option<&str>,
) -> PyResult<RawResponse> {
    let full_url = url_with_params(url, &params);

    // Build agent — always set an explicit TlsConnector so native-tls
    // properly loads the trust store on every platform.
    let mut agent_builder = ureq::AgentBuilder::new();

    let tls = build_tls_connector(verify)?;
    agent_builder = agent_builder.tls_connector(std::sync::Arc::new(tls));

    if let Some(t) = timeout {
        let dur = Duration::from_secs_f64(t);
        // Only set connect and read timeouts. Setting timeout_write (SO_SNDTIMEO) is
        // intentionally skipped: on gVisor the syscall causes the kernel to delay all
        // TCP transmission until the timer fires, breaking every request.
        agent_builder = agent_builder.timeout_connect(dur).timeout_read(dur);
    }

    if let Some(p) = proxy {
        let proxy_obj = ureq::Proxy::new(p)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        agent_builder = agent_builder.proxy(proxy_obj);
    }

    agent_builder = agent_builder.redirects(if allow_redirects { 30 } else { 0 });

    let agent = agent_builder.build();
    let mut req = agent.request(&method.to_uppercase(), &full_url);

    // Headers
    if let Some(hdrs) = headers {
        for (k, v) in &hdrs {
            req = req.set(k, v);
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
            req = req.set("Cookie", &s);
        }
    }

    // Basic auth
    if let Some((user, pass)) = auth {
        let creds = base64::engine::general_purpose::STANDARD.encode(format!("{}:{}", user, pass));
        req = req.set("Authorization", &format!("Basic {}", creds));
    }

    // Content-Type
    if let Some(ct) = content_type {
        req = req.set("Content-Type", ct);
    }

    let start = Instant::now();

    let result = if let Some(bytes) = body {
        req.send_bytes(bytes)
    } else {
        req.call()
    };

    let elapsed = start.elapsed().as_secs_f64();

    let resp = match result {
        Ok(r) => r,
        Err(ureq::Error::Status(_, r)) => r, // 4xx / 5xx — return as-is
        Err(ureq::Error::Transport(t)) => {
            let msg = t.to_string();
            return Err(match t.kind() {
                ureq::ErrorKind::Io => {
                    if msg.contains("timed out") || msg.contains("deadline") {
                        pyo3::exceptions::PyTimeoutError::new_err(msg)
                    } else {
                        pyo3::exceptions::PyConnectionError::new_err(msg)
                    }
                }
                ureq::ErrorKind::Dns | ureq::ErrorKind::ConnectionFailed => {
                    pyo3::exceptions::PyConnectionError::new_err(msg)
                }
                ureq::ErrorKind::TooManyRedirects => {
                    pyo3::exceptions::PyRuntimeError::new_err(format!("TooManyRedirects: {}", msg))
                }
                _ => pyo3::exceptions::PyRuntimeError::new_err(msg),
            });
        }
    };

    let status_code = resp.status();
    let final_url = resp.get_url().to_string();

    let mut headers_out: HashMap<String, String> = HashMap::new();
    for name in resp.headers_names() {
        if let Some(val) = resp.header(&name) {
            headers_out
                .entry(name.to_lowercase())
                .and_modify(|e| {
                    e.push_str(", ");
                    e.push_str(val);
                })
                .or_insert_with(|| val.to_string());
        }
    }

    let encoding = headers_out.get("content-type").and_then(|ct| {
        ct.split(';')
            .find(|s| s.trim().to_lowercase().starts_with("charset="))
            .map(|s| s.trim()["charset=".len()..].trim().to_string())
    });

    let mut body_bytes = Vec::new();
    resp.into_reader()
        .read_to_end(&mut body_bytes)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

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

#[pymodule(name = "_pyureq")]
fn pyureq_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RawResponse>()?;
    m.add_class::<RustClient>()?;
    m.add_function(wrap_pyfunction!(http_request, m)?)?;
    m.add_function(wrap_pyfunction!(tcp_probe, m)?)?;
    Ok(())
}
