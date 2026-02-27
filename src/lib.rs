use base64::Engine as _;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use std::collections::HashMap;
use std::io::Read;
use std::time::{Duration, Instant};

// ---------------------------------------------------------------------------
// RawResponse
// ---------------------------------------------------------------------------

#[pyclass(module = "rupy._rupy")]
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

#[pyclass(module = "rupy._rupy")]
pub struct RustClient {
    verify: bool,
    default_timeout: Option<f64>,
}

#[pymethods]
impl RustClient {
    #[new]
    #[pyo3(signature = (verify=true, timeout=None, no_proxy_all=false))]
    fn new(verify: bool, timeout: Option<f64>, no_proxy_all: bool) -> PyResult<Self> {
        let _ = no_proxy_all; // ureq handles NO_PROXY automatically
        Ok(RustClient { verify, default_timeout: timeout })
    }

    #[pyo3(signature = (method, url, headers=None, params=None, body=None, content_type=None, auth=None, timeout=None, allow_redirects=true, cookies=None))]
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
    ) -> PyResult<RawResponse> {
        let effective_timeout = timeout.or(self.default_timeout);
        // Clone/copy all data needed before releasing the GIL.
        let method = method.to_string();
        let url = url.to_string();
        let body_owned: Option<Vec<u8>> = body.map(|b| b.to_vec());
        let content_type = content_type.map(|s| s.to_string());
        let verify = self.verify;
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
                verify,
                allow_redirects,
                cookies,
            )
        })
    }
}

// ---------------------------------------------------------------------------
// Module-level one-shot function
// ---------------------------------------------------------------------------

#[pyfunction]
#[pyo3(signature = (method, url, headers=None, params=None, body=None, content_type=None, auth=None, timeout=None, verify=true, allow_redirects=true, cookies=None, no_proxy_all=false))]
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
    verify: bool,
    allow_redirects: bool,
    cookies: Option<HashMap<String, String>>,
    no_proxy_all: bool,
) -> PyResult<RawResponse> {
    let _ = no_proxy_all; // ureq reads NO_PROXY from env automatically
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
            verify,
            allow_redirects,
            cookies,
        )
    })
}

// ---------------------------------------------------------------------------
// Shared implementation (ureq — pure blocking, no tokio)
// ---------------------------------------------------------------------------

fn url_with_params(base: &str, params: &Option<Vec<(String, String)>>) -> String {
    let Some(p) = params else { return base.to_string() };
    if p.is_empty() { return base.to_string(); }
    let sep = if base.contains('?') { '&' } else { '?' };
    let qs: Vec<String> = p.iter()
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

fn http_execute(
    method: &str,
    url: &str,
    headers: Option<HashMap<String, String>>,
    params: Option<Vec<(String, String)>>,
    body: Option<&[u8]>,
    content_type: Option<&str>,
    auth: Option<(String, String)>,
    timeout: Option<f64>,
    verify: bool,
    allow_redirects: bool,
    cookies: Option<HashMap<String, String>>,
) -> PyResult<RawResponse> {
    let full_url = url_with_params(url, &params);

    // Build agent
    let mut agent_builder = ureq::AgentBuilder::new();

    if !verify {
        let tls = native_tls::TlsConnector::builder()
            .danger_accept_invalid_certs(true)
            .danger_accept_invalid_hostnames(true)
            .build()
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
        agent_builder = agent_builder.tls_connector(std::sync::Arc::new(tls));
    }

    if let Some(t) = timeout {
        let dur = Duration::from_secs_f64(t);
        // Only set connect and read timeouts. Setting timeout_write (SO_SNDTIMEO) is
        // intentionally skipped: on gVisor the syscall causes the kernel to delay all
        // TCP transmission until the timer fires, breaking every request.
        agent_builder = agent_builder
            .timeout_connect(dur)
            .timeout_read(dur);
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
            let s: String = cks.iter().map(|(k, v)| format!("{}={}", k, v)).collect::<Vec<_>>().join("; ");
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
                .and_modify(|e| { e.push_str(", "); e.push_str(val); })
                .or_insert_with(|| val.to_string());
        }
    }

    let encoding = headers_out.get("content-type").and_then(|ct| {
        ct.split(';')
            .find(|s| s.trim().to_lowercase().starts_with("charset="))
            .map(|s| s.trim()["charset=".len()..].trim().to_string())
    });

    let mut body_bytes = Vec::new();
    resp.into_reader().read_to_end(&mut body_bytes)
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

#[pymodule(name = "_rupy")]
fn rupy_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RawResponse>()?;
    m.add_class::<RustClient>()?;
    m.add_function(wrap_pyfunction!(http_request, m)?)?;
    m.add_function(wrap_pyfunction!(tcp_probe, m)?)?;
    Ok(())
}
