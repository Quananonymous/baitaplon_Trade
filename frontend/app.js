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
            <main class="content">
              ${innerHTML}
            </main>
          </div>
        </div>
    `;

    document.getElementById("btn-logout").addEventListener("click", () => {
        authToken = null;
        currentUsername = null;
        localStorage.removeItem("authToken");
        localStorage.removeItem("username");
        renderAuthScreen();
    });

    setupSidebarEvents();
}

// ================= Auth screen =================
function renderAuthScreen(message = "") {
    const app = document.getElementById("app");
    app.innerHTML = `
      <div class="auth-container">
        <div class="auth-card">
          <h1>QUAN TERMINAL</h1>
          <p style="color:#9ca3af;">Binance Futures dashboard (có thể dùng thật, cẩn thận vốn)</p>
          ${message ? `<p class="auth-message">${message}</p>` : ""}
          <form id="login-form">
            <div class="form-group">
              <label>Username</label>
              <input name="username" required />
            </div>
            <div class="form-group">
              <label>Password</label>
              <input name="password" type="password" required />
            </div>
            <div class="btn-row">
              <button class="btn btn-primary" type="submit" data-action="login">Đăng nhập</button>
              <button class="btn btn-secondary" type="button" id="btn-register">Đăng ký & đăng nhập</button>
            </div>
          </form>
        </div>
      </div>
    `;

    const form = document.getElementById("login-form");
    const btnRegister = document.getElementById("btn-register");

    form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const fd = new FormData(form);
        const username = fd.get("username").trim();
        const password = fd.get("password").trim();
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

    btnRegister.addEventListener("click", async () => {
        const fd = new FormData(form);
        const username = fd.get("username").trim();
        const password = fd.get("password").trim();
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
            renderAuthScreen("Register failed: " + err.message);
        }
    });
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
                <option value="1s">1 giây</option>
                <option value="1m">1 phút</option>
                <option value="5m">5 phút</option>
                <option value="15m">15 phút</option>
                <option value="1h">1 giờ</option>
                <option value="4h">4 giờ</option>
                <option value="1d">1 ngày</option>
              </select>
              <button class="btn btn-secondary" id="btn-apply-symbol">Đổi coin</button>
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

    // 👇 THÊM MỚI
    const symbolInput = document.getElementById("symbol-input");
    const btnApplySymbol = document.getElementById("btn-apply-symbol");
    const intervalSelect = document.getElementById("interval-select");

    if (symbolInput) {
        symbolInput.value = currentSymbol || "BTCUSDT";
    }
    if (intervalSelect) {
        intervalSelect.value = currentInterval || "1s";
    }

    if (btnApplySymbol && symbolInput) {
        btnApplySymbol.addEventListener("click", () => {
            const sym = symbolInput.value.trim().toUpperCase();
            if (!sym) return;
            currentSymbol = sym;

            // reset dữ liệu chart khi đổi coin
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
        intervalSelect.addEventListener("change", () => {
            const tf = intervalSelect.value || "1s";
            currentInterval = tf;

            // reset dữ liệu chart khi đổi khung thời gian
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
    // 👆 HẾT PHẦN THÊM

    initChart();
    connectPriceWS();  // kết nối WS giá với currentSymbol & currentInterval
    connectPnlWS();
    loadBotStatus();
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
          <form id="config-form" class="form">
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
              <label>Take Profit (%)</label>
              <input type="number" name="tp" value="10" />
            </div>
            <div class="form-group">
              <label>Stop Loss (%)</label>
              <input type="number" name="sl" value="20" />
            </div>
            <div class="form-group">
              <label>ROI Trigger (%) (optional)</label>
              <input type="number" name="roi_trigger" placeholder="VD: 5" />
            </div>
            <div class="form-group">
              <label>Số bot / số coin tối đa</label>
              <input type="number" name="bot_count" value="1" />
            </div>
            <button class="btn btn-primary" type="submit">Lưu config</button>
          </form>
        </div>
      </section>
    `);

    try {
        const cfg = await apiRequest("/api/bot-config");
        const form = document.getElementById("config-form");
        form.bot_mode.value = cfg.bot_mode || "static";
        form.symbol.value = cfg.symbol || "";
        form.lev.value = cfg.lev ?? 10;
        form.percent.value = cfg.percent ?? 5;
        form.tp.value = cfg.tp ?? 10;
        form.sl.value = cfg.sl ?? 20;
        form.roi_trigger.value = cfg.roi_trigger ?? "";
        form.bot_count.value = cfg.bot_count ?? 1;
    } catch (err) {
        console.error("Load config error:", err);
    }

    const form = document.getElementById("config-form");
    form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const fd = new FormData(form);
        const payload = {
            bot_mode: fd.get("bot_mode"),
            symbol: fd.get("symbol") || null,
            lev: Number(fd.get("lev") || 10),
            percent: Number(fd.get("percent") || 5),
            tp: Number(fd.get("tp") || 10),
            sl: Number(fd.get("sl") || 20),
            roi_trigger: fd.get("roi_trigger")
                ? Number(fd.get("roi_trigger"))
                : null,
            bot_count: Number(fd.get("bot_count") || 1),
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
}

// ================= API Key screen =================
async function renderApiKeyScreen(message = "") {
    renderBaseLayout(`
      <section class="panel">
        <div class="panel-header">
          <h2>API Binance</h2>
          <p>Nhập API Key/Secret của bạn (khuyến nghị dùng tài khoản testnet nếu có).</p>
        </div>
        <div class="panel-body">
          ${message ? `<p class="status">${message}</p>` : ""}
          <form id="apikey-form" class="form">
            <div class="form-group">
              <label>API Key</label>
              <input name="api_key" required />
            </div>
            <div class="form-group">
              <label>API Secret</label>
              <input name="api_secret" type="password" required />
            </div>
            <button class="btn btn-primary" type="submit">Lưu API</button>
          </form>
        </div>
      </section>
    `);

    // load trạng thái
    try {
        const st = await apiRequest("/api/setup-account");
        if (st.has_api) {
            const form = document.getElementById("apikey-form");
            form.api_key.placeholder = "(đã có API key)";
        }
    } catch (err) {
        console.error("Load setup-account error", err);
    }

    const form = document.getElementById("apikey-form");
    form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const fd = new FormData(form);
        const api_key = fd.get("api_key").trim();
        const api_secret = fd.get("api_secret").trim();

        try {
            await apiRequest("/api/setup-account", {
                method: "POST",
                body: { api_key, api_secret },
            });
            renderApiKeyScreen("Đã lưu API Key/Secret.");
        } catch (err) {
            renderApiKeyScreen("Lỗi lưu API: " + err.message);
        }
    });
}

// ================= Bot status =================
async function loadBotStatus() {
    try {
        const st = await apiRequest("/api/bot-status");
        const el = document.getElementById("bot-status-text");
        if (!el) return;

        if (st.running) {
            const botCount = st.bot_count ?? 0;
            const syms = (st.active_symbols && st.active_symbols.length)
                ? st.active_symbols.join(", ")
                : (st.symbol || "auto");

            let html =
                `Bot đang chạy | bots: ${botCount} | symbols: ${syms} | mode: ${st.mode}`;

            // Nếu backend trả về danh sách từng bot
            if (st.bots && Array.isArray(st.bots) && st.bots.length > 0) {
                html += '<ul class="bot-list">';
                for (const b of st.bots) {
                    const bSyms = (b.active_symbols && b.active_symbols.length)
                        ? b.active_symbols.join(", ")
                        : (b.symbol || "auto");
                    const bStatus = b.status || "unknown";
                    const strategy = b.strategy || "";
                    html += `
<li>
  <b>${b.id}</b>
  ${strategy ? ` | ${strategy}` : ""}
  | symbols: ${bSyms}
  | status: ${bStatus}
  <button class="btn btn-xs btn-danger btn-stop-one" data-bot-id="${b.id}">Dừng bot này</button>
</li>`;
                }
                html += "</ul>";
            }

            el.innerHTML = html;
            el.className = "status ok";

            // Gắn sự kiện dừng từng bot
            document.querySelectorAll(".btn-stop-one").forEach((btn) => {
                btn.addEventListener("click", async () => {
                    const botId = btn.getAttribute("data-bot-id");
                    if (!botId) return;
                    if (!confirm(`Dừng bot ${botId}?`)) return;
                    try {
                        await apiRequest("/api/bot-stop-one", {
                            method: "POST",
                            body: { bot_id: botId },
                        });
                        loadBotStatus();
                    } catch (err) {
                        alert("Lỗi dừng bot: " + err.message);
                    }
                });
            });
        } else {
            el.textContent = `Bot đang tắt. (mode: ${st.mode}, symbol: ${st.symbol || "auto"})`;
            el.className = "status";
        }
    } catch (err) {
        console.error("Load bot status error", err);
    }
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
                    borderWidth: 1,
                    pointRadius: 0,
                    tension: 0.1,
                },
            ],
        },
        options: {
            responsive: true,
            animation: false,
            scales: {
                x: {
                    display: true,
                },
                y: {
                    display: true,
                },
            },
            plugins: {
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

    const url = (location.protocol === "https:" ? "wss://" : "ws://") +
        location.host +
        `/ws/price?token=${encodeURIComponent(authToken)}&symbol=${encodeURIComponent(currentSymbol)}&interval=${encodeURIComponent(currentInterval)}`;

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
            // hiển thị theo kiểu HH:MM (nếu khung >= 1m thì giây không quan trọng)
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
    const url = (location.protocol === "https:" ? "wss://" : "ws://") +
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
            el.textContent = `${data.balance?.toFixed
                ? data.balance.toFixed(2)
                : data.balance} USDT`;
        } catch (e) {
            console.error("WS pnl parse error", e);
        }
    };
    wsPnl.onclose = () => {
        console.log("WS pnl closed");
    };
}

// =============== INIT ===============
function init() {
    authToken = localStorage.getItem("authToken");
    currentUsername = localStorage.getItem("username");
    if (authToken && currentUsername) afterLogin();
    else renderAuthScreen();
}

init();
