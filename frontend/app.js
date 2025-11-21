// app.js

// Nếu backend deploy riêng domain: đổi API_BASE = "https://your-api.com"
const API_BASE = "";

// Token & user toàn cục
let authToken = null;
let currentUsername = null;

// Chart & WebSocket
let priceChart = null;
let priceData = [];
let labelData = [];
let ws = null;
let keepAliveIntervalId = null;
let currentSymbol = "BTCUSDT"; // 👈 coin đang vẽ biểu đồ
let currentInterval = "1s"; // 👈 khung thời gian: 1s,1m,5m,15m,1h,4h,1d

// ================= API helper =================
async function apiRequest(path, { method = "GET", body = null, auth = true } = {}) {
    const url = API_BASE + path;
    const headers = { "Content-Type": "application/json" };
    if (auth && authToken) headers["X-Auth-Token"] = authToken;

    const res = await fetch(url, {
        method,
        headers,
        body: body ? JSON.stringify(body) : null,
    });
    if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `HTTP ${res.status}`);
    }
    return res.json();
}

function startKeepAlive() {
    if (keepAliveIntervalId) {
        clearInterval(keepAliveIntervalId);
    }
    keepAliveIntervalId = setInterval(async () => {
        try {
            await apiRequest("/api/ping", { method: "GET", auth: false });
            console.log("keep-alive ping");
        } catch (e) {
            console.warn("keep-alive ping error", e);
        }
    }, 20000);
}

// ================= DOM helper =================
function $(selector) {
    return document.querySelector(selector);
}

// ================= Layout render =================
function renderBaseLayout(innerHTML) {
    const app = document.getElementById("app");
    app.innerHTML = `
        <div class="app">
          <div class="topbar">
            <div class="topbar-left">
              <div class="logo-circle">Q</div>
              <div>
                <div class="topbar-title">QUAN TERMINAL</div>
                <div class="topbar-sub">Paper trading / demo Binance futures</div>
              </div>
            </div>
            <div class="topbar-right">
              <div class="user-chip">
                <div class="user-avatar">${(currentUsername || "U")[0].toUpperCase()}</div>
                <div>
                  <div>${currentUsername || "Guest"}</div>
                  <div style="font-size:11px;color:#9ca3af;">Binance Futures (real, cẩn thận rủi ro)</div>
                </div>
              </div>
            </div>
          </div>
          <div class="shell">
            <aside class="sidebar">
              <div>
                <div class="sidebar-section-title">Main</div>
                <button class="sidebar-btn" data-screen="dashboard">📊 Dashboard</button>
                <button class="sidebar-btn" data-screen="config">⚙️ Cấu hình bot</button>
                <button class="sidebar-btn" data-screen="apikey">🔑 API Binance</button>
              </div>
              <div>
                <button class="sidebar-btn danger" id="btn-logout">Đăng xuất</button>
              </div>
            </aside>
            <main class="main">
              ${innerHTML}
            </main>
          </div>
        </div>
    `;

    setupSidebarEvents();
    const logoutBtn = document.getElementById("btn-logout");
    if (logoutBtn) {
        logoutBtn.addEventListener("click", () => {
            authToken = null;
            currentUsername = null;
            localStorage.removeItem("authToken");
            localStorage.removeItem("username");
            renderAuthScreen();
        });
    }
}

// ================= Auth screen =================
function renderAuthScreen(message = "") {
    const app = document.getElementById("app");
    app.innerHTML = `
      <div class="auth-wrapper">
        <div class="auth-panel">
          <h1>QUAN Terminal</h1>
          <p class="sub">Demo Binance Futures trading dashboard</p>
          ${message ? `<p class="status">${message}</p>` : ""}
          <form id="login-form">
            <div class="form-group">
              <label>Username</label>
              <input id="login-username" type="text" required />
            </div>
            <div class="form-group">
              <label>Password</label>
              <input id="login-password" type="password" required />
            </div>
            <div class="form-actions">
              <button type="submit" class="btn btn-primary">Đăng nhập</button>
              <button type="button" class="btn btn-secondary" id="btn-register">Đăng ký</button>
            </div>
          </form>
        </div>
      </div>
    `;

    const form = document.getElementById("login-form");
    form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const username = document.getElementById("login-username").value.trim();
        const password = document.getElementById("login-password").value.trim();
        try {
            const data = await apiRequest("/api/login", {
                method: "POST",
                body: { username, password },
                auth: false,
            });
            authToken = data.token;
            currentUsername = data.username;
            localStorage.setItem("authToken", authToken);
            localStorage.setItem("username", currentUsername);
            afterLogin();
        } catch (err) {
            renderAuthScreen("Login failed: " + err.message);
        }
    });

    const btnRegister = document.getElementById("btn-register");
    btnRegister.addEventListener("click", () => renderRegisterScreen());
}

function renderRegisterScreen(message = "") {
    const app = document.getElementById("app");
    app.innerHTML = `
      <div class="auth-wrapper">
        <div class="auth-panel">
          <h1>Register</h1>
          ${message ? `<p class="status">${message}</p>` : ""}
          <form id="register-form">
            <div class="form-group">
              <label>Username</label>
              <input id="reg-username" type="text" required />
            </div>
            <div class="form-group">
              <label>Password</label>
              <input id="reg-password" type="password" required />
            </div>
            <div class="form-actions">
              <button type="submit" class="btn btn-primary">Đăng ký</button>
              <button type="button" class="btn btn-secondary" id="btn-back">Quay lại</button>
            </div>
          </form>
        </div>
      </div>
    `;

    const form = document.getElementById("register-form");
    form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const username = document.getElementById("reg-username").value.trim();
        const password = document.getElementById("reg-password").value.trim();
        try {
            const data = await apiRequest("/api/register", {
                method: "POST",
                body: { username, password },
                auth: false,
            });
            authToken = data.token;
            currentUsername = data.username;
            localStorage.setItem("authToken", authToken);
            localStorage.setItem("username", currentUsername);
            afterLogin();
        } catch (err) {
            renderRegisterScreen("Register failed: " + err.message);
        }
    });

    const btnBack = document.getElementById("btn-back");
    btnBack.addEventListener("click", () => renderAuthScreen());
}

function afterLogin() {
    renderDashboard();
}

// ================= Sidebar =================
function setupSidebarEvents() {
    document.querySelectorAll(".sidebar-btn[data-screen]").forEach((btn) => {
        btn.addEventListener("click", () => changeScreen(btn.dataset.screen));
    });
}

function changeScreen(screen) {
    if (screen === "dashboard") {
        renderDashboard();
    } else if (screen === "config") {
        renderConfigScreen();
    } else if (screen === "apikey") {
        renderApiKeyScreen();
    }
}

// ================= Dashboard =================
function renderDashboard() {
    renderBaseLayout(`
      <div class="dashboard">
        <section class="panel">
          <div class="panel-header">
            <div>
              <h2>Giá Futures realtime</h2>
              <p>Giá Futures lấy từ Binance, chọn coin / khung thời gian bên phải và cập nhật qua WebSocket.</p>
            </div>
            <div class="panel-actions">
              <input
                id="symbol-input"
                class="input"
                placeholder="BTCUSDT"
                value="BTCUSDT"
                style="width:120px;margin-right:8px;"
              />
              <select
                id="interval-select"
                class="input"
                style="width:130px;margin-right:8px;"
              >
                <option value="1s">1s (ticker)</option>
                <option value="1m">1m (nến)</option>
                <option value="5m">5m</option>
                <option value="15m">15m</option>
                <option value="1h">1h</option>
                <option value="4h">4h</option>
                <option value="1d">1d</option>
              </select>
              <button class="btn btn-secondary" id="btn-bot-start">Start bot</button>
              <button class="btn btn-secondary" id="btn-bot-stop">Stop bot</button>
            </div>
          </div>
          <div class="chart-wrapper">
            <canvas id="price-chart"></canvas>
          </div>
          <div class="panel-footer">
            <div id="bot-status-text" class="status"></div>
          </div>
        </section>
        <section class="panel">
          <div class="panel-header">
            <h2>Số dư Futures</h2>
            <p>Số dư tài khoản Futures (availableBalance) cập nhật realtime qua WebSocket.</p>
          </div>
          <div class="panel-body">
            <div id="pnl-balance" class="pnl-balance">Loading...</div>
          </div>
        </section>
      </div>
    `);

    document.getElementById("btn-bot-start").addEventListener("click", async () => {
        try {
            await apiRequest("/api/bot-start", { method: "POST" });
            loadBotStatus();
        } catch (err) {
            alert("Lỗi start bot: " + err.message);
        }
    });

    document.getElementById("btn-bot-stop").addEventListener("click", async () => {
        try {
            await apiRequest("/api/bot-stop", { method: "POST" });
            loadBotStatus();
        } catch (err) {
            alert("Lỗi stop bot: " + err.message);
        }
    });

    const symbolInput = document.getElementById("symbol-input");
    const intervalSelect = document.getElementById("interval-select");

    if (symbolInput) {
        symbolInput.addEventListener("change", () => {
            const sym = symbolInput.value.trim().toUpperCase() || "BTCUSDT";
            currentSymbol = sym;

            priceData = [];
            labelData = [];
            if (priceChart) {
                priceChart.data.labels = labelData;
                priceChart.data.datasets[0].data = priceData;
                priceChart.update();
            }

            connectPriceWS();
        });
    }

    if (intervalSelect) {
        intervalSelect.value = currentInterval || "1s";
        intervalSelect.addEventListener("change", () => {
            const tf = intervalSelect.value || "1s";
            currentInterval = tf;

            priceData = [];
            labelData = [];
            if (priceChart) {
                priceChart.data.labels = labelData;
                priceChart.data.datasets[0].data = priceData;
                priceChart.update();
            }

            connectPriceWS();
        });
    }

    initChart();
    connectPriceWS();
    connectPnlWS();
    loadBotStatus();
}

// ================= Chart =================
function initChart() {
    const ctx = document.getElementById("price-chart");
    if (!ctx) return;

    priceData = [];
    labelData = [];

    priceChart = new Chart(ctx, {
        type: "line",
        data: {
            labels: labelData,
            datasets: [
                {
                    label: "Price",
                    data: priceData,
                    borderWidth: 2,
                    tension: 0.2,
                    pointRadius: 0,
                },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: {
                    display: true,
                },
                y: {
                    display: true,
                },
            },
            plugins: {
                legend: {
                    display: false,
                },
                title: {
                    display: true,
                    text: currentSymbol + " (" + currentInterval + ")",
                },
            },
        },
    });
}

// =============== WebSocket ===============
function connectPriceWS() {
    if (!currentSymbol) {
        currentSymbol = "BTCUSDT";
    }
    if (!currentInterval) {
        currentInterval = "1s";
    }

    if (ws) {
        ws.close();
        ws = null;
    }

    const url =
        (location.protocol === "https:" ? "wss://" : "ws://") +
        location.host +
        `/ws/price?token=${encodeURIComponent(authToken)}&symbol=${encodeURIComponent(
            currentSymbol
        )}&interval=${encodeURIComponent(currentInterval)}`;

    ws = new WebSocket(url);
    ws.onopen = () => {
        console.log("WS price connected for", currentSymbol, "interval", currentInterval);
    };
    ws.onmessage = (ev) => {
        try {
            const data = JSON.parse(ev.data);
            if (data.error) {
                console.error("WS price error:", data);
                return;
            }

            if (priceData.length > 200) {
                priceData.shift();
                labelData.shift();
            }

            priceData.push(data.price);
            const t = new Date(data.timestamp * 1000);
            const label =
                `${t.getHours()}:${String(t.getMinutes()).padStart(2, "0")}` +
                (currentInterval === "1s"
                    ? `:${String(t.getSeconds()).padStart(2, "0")}`
                    : "");
            labelData.push(label);

            if (priceChart) {
                if (
                    priceChart.options &&
                    priceChart.options.plugins &&
                    priceChart.options.plugins.title
                ) {
                    priceChart.options.plugins.title.text =
                        currentSymbol + " (" + currentInterval + ")";
                }
                priceChart.update("none");
            }
        } catch (e) {
            console.error("WS parse error", e);
        }
    };
    ws.onclose = () => {
        console.log("WS price closed");
    };
}

function connectPnlWS() {
    if (!authToken) return;
    const url =
        (location.protocol === "https:" ? "wss://" : "ws://") +
        location.host +
        `/ws/pnl?token=${encodeURIComponent(authToken)}`;

    const wsPnl = new WebSocket(url);
    wsPnl.onopen = () => {
        console.log("WS pnl connected");
    };
    wsPnl.onmessage = (ev) => {
        try {
            const data = JSON.parse(ev.data);
            const el = document.getElementById("pnl-balance");
            if (!el) return;
            if (data.error) {
                el.textContent = "Error: " + (data.message || data.error);
                return;
            }
            el.textContent = `${
                data.balance?.toFixed ? data.balance.toFixed(2) : data.balance
            } USDT`;
        } catch (e) {
            console.error("WS pnl parse error", e);
        }
    };
    wsPnl.onclose = () => {
        console.log("WS pnl closed");
    };
}

// ================= Bot status =================
async function loadBotStatus() {
    try {
        const data = await apiRequest("/api/bot-status");
        const el = document.getElementById("bot-status-text");
        if (!el) return;
        if (!data.running) {
            el.textContent = "Bot đang dừng.";
        } else {
            el.textContent = `Bot đang chạy. Số bot: ${
                data.bots ? data.bots.length : "?"
            }`;
        }
    } catch (err) {
        console.error("Lỗi loadBotStatus:", err);
    }
}

// ================= Cấu hình bot =================
async function renderConfigScreen(message = "") {
    renderBaseLayout(`
      <section class="panel">
        <div class="panel-header">
          <h2>Cấu hình Bot</h2>
          <p>Thiết lập chiến lược & thông số để BotManager (trading_bot_lib) sử dụng.</p>
        </div>
        <div class="panel-body">
          ${message ? `<p class="status">${message}</p>` : ""}
          <form id="config-form" class="form-grid">
            <div class="form-group">
              <label>Chế độ bot</label>
              <select name="bot_mode">
                <option value="static">Static (1 coin cố định)</option>
                <option value="dynamic">Dynamic (tự quét nhiều coin)</option>
              </select>
            </div>
            <div class="form-group">
              <label>Symbol (nếu static)</label>
              <input type="text" name="symbol" placeholder="BTCUSDT" />
            </div>
            <div class="form-group">
              <label>Đòn bẩy (lev)</label>
              <input type="number" name="lev" value="10" />
            </div>
            <div class="form-group">
              <label>% vốn mỗi lệnh</label>
              <input type="number" name="percent" value="5" />
            </div>
            <div class="form-group">
              <label>TP (%)</label>
              <input type="number" name="tp" value="10" />
            </div>
            <div class="form-group">
              <label>SL (%)</label>
              <input type="number" name="sl" value="20" />
            </div>
            <div class="form-group">
              <label>ROI trigger (tuỳ chọn)</label>
              <input type="number" name="roi_trigger" placeholder="ví dụ 30" />
            </div>
            <div class="form-group">
              <label>Số bot (bot_count)</label>
              <input type="number" name="bot_count" value="1" />
            </div>
            <div class="form-actions">
              <button type="submit" class="btn btn-primary">Lưu cấu hình</button>
            </div>
          </form>
        </div>
      </section>
    `);

    try {
        const cfg = await apiRequest("/api/bot-config");
        const form = document.getElementById("config-form");
        if (cfg && form) {
            form.elements["bot_mode"].value = cfg.bot_mode || "static";
            form.elements["symbol"].value = cfg.symbol || "BTCUSDT";
            form.elements["lev"].value = cfg.lev ?? 10;
            form.elements["percent"].value = cfg.percent ?? 5;
            form.elements["tp"].value = cfg.tp ?? 10;
            form.elements["sl"].value = cfg.sl ?? 20;
            form.elements["roi_trigger"].value =
                cfg.roi_trigger !== null && cfg.roi_trigger !== undefined
                    ? cfg.roi_trigger
                    : "";
            form.elements["bot_count"].value = cfg.bot_count ?? 1;
        }

        form.addEventListener("submit", async (e) => {
            e.preventDefault();
            const payload = {
                bot_mode: form.elements["bot_mode"].value,
                symbol: form.elements["symbol"].value.trim() || null,
                lev: Number(form.elements["lev"].value),
                percent: Number(form.elements["percent"].value),
                tp: Number(form.elements["tp"].value),
                sl: Number(form.elements["sl"].value),
                roi_trigger: form.elements["roi_trigger"].value
                    ? Number(form.elements["roi_trigger"].value)
                    : null,
                bot_count: Number(form.elements["bot_count"].value),
            };
            try {
                await apiRequest("/api/bot-config", {
                    method: "POST",
                    body: payload,
                });
                renderConfigScreen("Đã lưu cấu hình.");
            } catch (err) {
                renderConfigScreen("Lỗi lưu cấu hình: " + err.message);
            }
        });
    } catch (err) {
        renderConfigScreen("Lỗi load cấu hình: " + err.message);
    }
}

// ================= API KEY SCREEN =================
async function renderApiKeyScreen(message = "") {
    renderBaseLayout(`
      <section class="panel">
        <div class="panel-header">
          <h2>API Binance</h2>
          <p>Nhập API Key & Secret cho Binance Futures (cẩn thận không share cho ai khác).</p>
        </div>
        <div class="panel-body">
          ${message ? `<p class="status">${message}</p>` : ""}
          <form id="api-form">
            <div class="form-group">
              <label>API Key</label>
              <input type="text" name="api_key" required />
            </div>
            <div class="form-group">
              <label>API Secret</label>
              <input type="password" name="api_secret" required />
            </div>
            <div class="form-actions">
              <button type="submit" class="btn btn-primary">Lưu API</button>
            </div>
          </form>
        </div>
      </section>
    `);

    const form = document.getElementById("api-form");
    form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const api_key = form.elements["api_key"].value.trim();
        const api_secret = form.elements["api_secret"].value.trim();
        try {
            await apiRequest("/api/setup-account", {
                method: "POST",
                body: { api_key, api_secret },
            });
            renderApiKeyScreen("Đã lưu API thành công.");
        } catch (err) {
            renderApiKeyScreen("Lỗi lưu API: " + err.message);
        }
    });
}

// =============== INIT ===============
function init() {
    authToken = localStorage.getItem("authToken");
    currentUsername = localStorage.getItem("username");
    startKeepAlive();
    if (authToken && currentUsername) afterLogin();
    else renderAuthScreen();
}

init();
