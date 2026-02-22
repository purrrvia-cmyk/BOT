// =====================================================
// ICT Trading Bot - Frontend JavaScript
// =====================================================

// WebSocket bağlantısı
const socket = io();
let botRunning = false;
let updateInterval = null;

// =================== WEBSOCKET ===================

socket.on("connect", () => {
    updateConnectionStatus(true);
    socket.emit("request_update");
});

socket.on("disconnect", () => {
    updateConnectionStatus(false);
});

socket.on("bot_status", (data) => {
    botRunning = data.running;
    updateBotButton();
});

socket.on("new_signal", (data) => {
    showToast(
        `Yeni Sinyal: ${data.symbol} ${data.direction}`,
        data.status === "OPENED" ? "success" : "info"
    );
    loadActiveSignals();
    loadPerformance();
});

socket.on("trade_closed", (data) => {
    const isWon = data.status === "WON";
    showToast(
        `${data.symbol} ${isWon ? "KAZANDI" : "KAYBETTİ"} | PnL: ${data.pnl_pct}%`,
        isWon ? "success" : "error"
    );
    loadActiveSignals();
    loadHistory();
    loadPerformance();
});

socket.on("watch_promoted", (data) => {
    showToast(`${data.symbol} izlemeden sinyale yükseltildi!`, "info");
    loadActiveSignals();
    loadWatchlist();
});

socket.on("scan_complete", (data) => {
    document.getElementById("lastScanTime").textContent =
        `Son Tarama: ${formatTime(data.timestamp)}`;
    document.getElementById("scanCount").textContent =
        `Tarama: ${data.symbols_scanned} coin`;
});

socket.on("optimization_done", (data) => {
    if (data.changes && data.changes.length > 0) {
        showToast(
            `Optimizasyon: ${data.changes.length} parametre güncellendi`,
            "warning"
        );
        loadOptimization();
    }
});

socket.on("full_update", (data) => {
    if (data.stats) updateStats(data.stats);
});

socket.on("trades_updated", () => {
    loadActiveSignals();
});

// =================== INIT ===================

document.addEventListener("DOMContentLoaded", () => {
    loadAllData();
    // Her 10 saniyede bir güncelle
    updateInterval = setInterval(() => {
        if (botRunning) {
            loadActiveSignals();
            loadPerformance();
        }
        updateServerTime();
    }, 10000);
    updateServerTime();
    setInterval(updateServerTime, 1000);
});

function loadAllData() {
    loadActiveSignals();
    loadCoins();
    loadWatchlist();
    loadHistory();
    loadPerformance();
    loadOptimization();
    loadBotStatus();
}

// =================== API CALLS ===================

async function apiFetch(url) {
    try {
        const res = await fetch(url);
        return await res.json();
    } catch (e) {
        console.error(`API Hatası (${url}):`, e);
        return null;
    }
}

async function apiPost(url) {
    try {
        const res = await fetch(url, { method: "POST" });
        return await res.json();
    } catch (e) {
        console.error(`API POST Hatası (${url}):`, e);
        return null;
    }
}

// =================== BOT KONTROLÜ ===================

async function toggleBot() {
    const btn = document.getElementById("btnToggleBot");
    btn.disabled = true;

    if (botRunning) {
        const result = await apiPost("/api/stop");
        if (result) {
            botRunning = false;
            showToast("Bot durduruldu", "info");
        }
    } else {
        const result = await apiPost("/api/start");
        if (result) {
            botRunning = true;
            showToast("Bot başlatıldı! Piyasa taranıyor...", "success");
        }
    }

    updateBotButton();
    btn.disabled = false;
}

async function loadBotStatus() {
    const data = await apiFetch("/api/status");
    if (data) {
        botRunning = data.running;
        updateBotButton();
        if (data.last_scan) {
            document.getElementById("lastScanTime").textContent =
                `Son Tarama: ${formatTime(data.last_scan)}`;
        }
    }
}

function updateBotButton() {
    const btn = document.getElementById("btnToggleBot");
    if (botRunning) {
        btn.className = "btn btn-stop";
        btn.innerHTML = '<i class="fas fa-stop"></i><span>Durdur</span>';
    } else {
        btn.className = "btn btn-start";
        btn.innerHTML = '<i class="fas fa-play"></i><span>Başlat</span>';
    }
}

function updateConnectionStatus(connected) {
    const el = document.getElementById("connectionStatus");
    if (connected) {
        el.innerHTML = '<span class="status-dot connected"></span><span>Bağlı</span>';
    } else {
        el.innerHTML = '<span class="status-dot disconnected"></span><span>Bağlantı kesildi</span>';
    }
}

// =================== AKTİF SİNYALLER ===================

async function loadActiveSignals() {
    const data = await apiFetch("/api/signals/active");
    if (!data) return;

    const tbody = document.getElementById("activeSignalsTable");

    // Stats güncelle
    const stats = await apiFetch("/api/performance");
    if (stats) updateStats(stats);

    if (data.length === 0) {
        tbody.innerHTML = `
            <tr class="empty-row"><td colspan="11">
                <div class="empty-state">
                    <i class="fas fa-inbox"></i>
                    <p>Henüz aktif sinyal yok</p>
                    <small>Bot başlatıldığında sinyaller burada görünecek</small>
                </div>
            </td></tr>`;
        return;
    }

    tbody.innerHTML = data.map(s => {
        const pnl = s.unrealized_pnl || 0;
        const pnlClass = pnl >= 0 ? "pnl-positive" : "pnl-negative";
        const pnlText = pnl >= 0 ? `+${pnl}%` : `${pnl}%`;
        const dirBadge = s.direction === "LONG"
            ? '<span class="badge badge-long"><i class="fas fa-arrow-up"></i> LONG</span>'
            : '<span class="badge badge-short"><i class="fas fa-arrow-down"></i> SHORT</span>';
        const statusBadge = s.status === "ACTIVE"
            ? '<span class="badge badge-active"><i class="fas fa-bolt"></i> Aktif</span>'
            : '<span class="badge badge-waiting"><i class="fas fa-clock"></i> Bekliyor</span>';
        const confidence = s.confidence || 0;
        const confClass = confidence >= 75 ? "high" : confidence >= 55 ? "medium" : "low";

        const coinParts = s.symbol.split("-");
        const coinDisplay = `<div class="coin-symbol"><span class="coin-name">${coinParts[0]}</span><span class="coin-pair">/${coinParts[1]}</span></div>`;

        return `<tr>
            <td>#${s.id}</td>
            <td>${coinDisplay}</td>
            <td>${dirBadge}</td>
            <td>${formatPrice(s.entry_price)}</td>
            <td>${s.current_price ? formatPrice(s.current_price) : "--"}</td>
            <td style="color:var(--accent-red)">${formatPrice(s.stop_loss)}</td>
            <td style="color:var(--accent-green)">${formatPrice(s.take_profit)}</td>
            <td>
                <div class="confidence-bar">
                    <div class="confidence-bar-track">
                        <div class="confidence-bar-fill ${confClass}" style="width:${confidence}%"></div>
                    </div>
                    <span style="font-size:11px;color:var(--text-secondary)">${confidence}%</span>
                </div>
            </td>
            <td><span class="${pnlClass}">${pnlText}</span></td>
            <td>${statusBadge}</td>
            <td>
                <button class="btn btn-danger" onclick="cancelSignal(${s.id})">
                    <i class="fas fa-times"></i> İptal
                </button>
            </td>
        </tr>`;
    }).join("");
}

async function cancelSignal(signalId) {
    if (!confirm("Bu sinyali iptal etmek istediğinize emin misiniz?")) return;
    await apiPost(`/api/signal/${signalId}/cancel`);
    showToast("Sinyal iptal edildi", "info");
    loadActiveSignals();
}

// =================== TARANAN COİNLER ===================

let allCoinsData = [];

async function loadCoins() {
    const data = await apiFetch("/api/coins");
    if (!data) return;

    allCoinsData = data.coins || [];
    document.getElementById("activeCoinCount").textContent = data.total_coins || 0;

    renderCoinsTable(allCoinsData);
}

function filterCoins() {
    const query = document.getElementById("coinSearchInput").value.toUpperCase().trim();
    if (!query) {
        renderCoinsTable(allCoinsData);
        return;
    }
    const filtered = allCoinsData.filter(c => c.symbol.toUpperCase().includes(query));
    renderCoinsTable(filtered);
}

function renderCoinsTable(coins) {
    const tbody = document.getElementById("coinsTable");

    if (!coins || coins.length === 0) {
        tbody.innerHTML = `
            <tr class="empty-row"><td colspan="6">
                <div class="empty-state">
                    <i class="fas fa-satellite-dish"></i>
                    <p>Coin bulunamadı</p>
                    <small>OKX'ten 5M+ hacimli coin verisi bekleniyor...</small>
                </div>
            </td></tr>`;
        return;
    }

    tbody.innerHTML = coins.map((coin, index) => {
        const parts = coin.symbol.split("-");
        const coinName = parts[0] || coin.symbol;
        const pairName = parts[1] || "USDT";
        const coinDisplay = `<div class="coin-symbol"><span class="coin-name">${coinName}</span><span class="coin-pair">/${pairName}</span></div>`;

        const price = coin.last_price || 0;
        const change = coin.change_pct || 0;
        const changeClass = change >= 0 ? "pnl-positive" : "pnl-negative";
        const changeIcon = change >= 0 ? "fa-arrow-up" : "fa-arrow-down";
        const changeText = change >= 0 ? `+${change.toFixed(2)}%` : `${change.toFixed(2)}%`;

        const volume = coin.volume_usdt || 0;
        const volText = volume >= 1_000_000_000
            ? `$${(volume / 1_000_000_000).toFixed(2)}B`
            : volume >= 1_000_000
            ? `$${(volume / 1_000_000).toFixed(2)}M`
            : `$${volume.toLocaleString("tr-TR")}`;

        const volBarPct = Math.min(100, (volume / (coins[0]?.volume_usdt || 1)) * 100);

        return `<tr>
            <td style="color:var(--text-muted);font-size:12px">${index + 1}</td>
            <td>${coinDisplay}</td>
            <td style="font-family:var(--font-mono);font-weight:600">${formatPrice(price)}</td>
            <td><span class="${changeClass}"><i class="fas ${changeIcon}" style="font-size:10px;margin-right:4px"></i>${changeText}</span></td>
            <td>
                <div class="volume-cell">
                    <span class="vol-text">${volText}</span>
                    <div class="vol-bar-track">
                        <div class="vol-bar-fill" style="width:${volBarPct}%"></div>
                    </div>
                </div>
            </td>
            <td>
                <button class="btn btn-sm btn-analyze" onclick="analyzeSymbol('${coin.symbol}')">
                    <i class="fas fa-search-plus"></i> Analiz
                </button>
            </td>
        </tr>`;
    }).join("");
}

async function analyzeSymbol(symbol) {
    showToast(`${symbol} analiz ediliyor...`, "info");
    const data = await apiFetch(`/api/analyze/${symbol}`);
    if (data && !data.error) {
        const score = data.confluence_score || 0;
        const dir = data.direction || "Belirsiz";
        const components = data.components || [];
        const compText = components.length > 0 ? ` | ${components.join(", ")}` : "";
        showToast(`${symbol}: Skor ${score.toFixed(1)}/100 | Yön: ${dir}${compText}`, score >= 60 ? "success" : "warning");
    } else {
        showToast(`${symbol} analiz edilemedi: ${data?.error || "bilinmeyen hata"}`, "error");
    }
}

// =================== İZLEME LİSTESİ ===================

async function loadWatchlist() {
    const data = await apiFetch("/api/watchlist");
    if (!data) return;

    const tbody = document.getElementById("watchlistTable");

    if (data.length === 0) {
        tbody.innerHTML = `
            <tr class="empty-row"><td colspan="8">
                <div class="empty-state">
                    <i class="fas fa-binoculars"></i>
                    <p>İzleme listesi boş</p>
                    <small>Potansiyel sinyaller burada bekletilir</small>
                </div>
            </td></tr>`;
        return;
    }

    tbody.innerHTML = data.map(item => {
        const dirBadge = item.direction === "LONG"
            ? '<span class="badge badge-long"><i class="fas fa-arrow-up"></i> LONG</span>'
            : '<span class="badge badge-short"><i class="fas fa-arrow-down"></i> SHORT</span>';

        const progressPct = Math.round((item.candles_watched / item.max_watch_candles) * 100);
        const scoreChange = item.current_score - item.initial_score;
        const scoreChangeText = scoreChange >= 0
            ? `<span class="pnl-positive">+${scoreChange.toFixed(1)}</span>`
            : `<span class="pnl-negative">${scoreChange.toFixed(1)}</span>`;

        const coinParts = item.symbol.split("-");
        const coinDisplay = `<div class="coin-symbol"><span class="coin-name">${coinParts[0]}</span><span class="coin-pair">/${coinParts[1]}</span></div>`;

        return `<tr>
            <td>${coinDisplay}</td>
            <td>${dirBadge}</td>
            <td>${formatPrice(item.potential_entry)}</td>
            <td>${item.initial_score ? item.initial_score.toFixed(1) : "--"}%</td>
            <td>${item.current_score ? item.current_score.toFixed(1) : "--"}% ${scoreChangeText}</td>
            <td>
                <div style="display:flex;align-items:center;gap:8px;">
                    <div class="confidence-bar-track" style="width:60px">
                        <div class="confidence-bar-fill medium" style="width:${progressPct}%"></div>
                    </div>
                    <span style="font-size:11px">${item.candles_watched}/${item.max_watch_candles}</span>
                </div>
            </td>
            <td style="font-size:11px;color:var(--text-secondary);font-family:var(--font-main);max-width:200px">${item.watch_reason || "--"}</td>
            <td><span class="badge badge-watching"><i class="fas fa-eye"></i> İzleniyor</span></td>
        </tr>`;
    }).join("");

    // Son expire edilenleri yükle
    loadExpired();
}

async function loadExpired() {
    const data = await apiFetch("/api/watchlist/expired?minutes=30");
    const section = document.getElementById("expiredSection");
    const list = document.getElementById("expiredList");

    if (!data || data.length === 0) {
        section.style.display = "none";
        return;
    }

    section.style.display = "block";
    list.innerHTML = data.map(item => {
        const coin = item.symbol.split("-")[0];
        const dir = item.direction === "LONG" ? "↑" : "↓";
        const reason = item.expire_reason || "Bilinmiyor";
        const time = item.updated_at ? new Date(item.updated_at).toLocaleTimeString("tr-TR", {hour:"2-digit", minute:"2-digit"}) : "";
        return `<div class="expired-chip">
            <span class="chip-coin">${coin} ${dir}</span>
            <span class="chip-reason">${reason}</span>
            <span class="chip-time">${time}</span>
        </div>`;
    }).join("");
}

// =================== GEÇMİŞ ===================

async function loadHistory() {
    const data = await apiFetch("/api/signals/history?limit=50");
    if (!data) return;

    const tbody = document.getElementById("historyTable");
    const completed = data.filter(s => s.status === "WON" || s.status === "LOST");
    const wins = completed.filter(s => s.status === "WON").length;
    const losses = completed.filter(s => s.status === "LOST").length;

    document.getElementById("histWins").textContent = wins;
    document.getElementById("histLosses").textContent = losses;

    if (data.length === 0) {
        tbody.innerHTML = `
            <tr class="empty-row"><td colspan="11">
                <div class="empty-state">
                    <i class="fas fa-archive"></i>
                    <p>Geçmiş işlem yok</p>
                </div>
            </td></tr>`;
        return;
    }

    tbody.innerHTML = data.map(s => {
        const dirBadge = s.direction === "LONG"
            ? '<span class="badge badge-long"><i class="fas fa-arrow-up"></i> LONG</span>'
            : '<span class="badge badge-short"><i class="fas fa-arrow-down"></i> SHORT</span>';

        let statusBadge = "";
        switch (s.status) {
            case "WON":
                statusBadge = '<span class="badge badge-won"><i class="fas fa-trophy"></i> Kazandı</span>';
                break;
            case "LOST":
                statusBadge = '<span class="badge badge-lost"><i class="fas fa-times"></i> Kaybetti</span>';
                break;
            case "CANCELLED":
                statusBadge = '<span class="badge badge-cancelled"><i class="fas fa-ban"></i> İptal</span>';
                break;
            case "ACTIVE":
                statusBadge = '<span class="badge badge-active"><i class="fas fa-bolt"></i> Aktif</span>';
                break;
            default:
                statusBadge = `<span class="badge badge-waiting">${s.status}</span>`;
        }

        const pnl = s.pnl_pct || 0;
        const pnlClass = pnl >= 0 ? "pnl-positive" : "pnl-negative";
        const pnlText = pnl !== 0 ? (pnl >= 0 ? `+${pnl.toFixed(2)}%` : `${pnl.toFixed(2)}%`) : "--";

        const coinParts = s.symbol.split("-");
        const coinDisplay = `<div class="coin-symbol"><span class="coin-name">${coinParts[0]}</span><span class="coin-pair">/${coinParts[1]}</span></div>`;

        return `<tr>
            <td>#${s.id}</td>
            <td>${coinDisplay}</td>
            <td>${dirBadge}</td>
            <td>${formatPrice(s.entry_price)}</td>
            <td>${s.close_price ? formatPrice(s.close_price) : "--"}</td>
            <td style="color:var(--accent-red)">${formatPrice(s.stop_loss)}</td>
            <td style="color:var(--accent-green)">${formatPrice(s.take_profit)}</td>
            <td><span class="${pnlClass}">${pnlText}</span></td>
            <td>${s.confidence ? s.confidence.toFixed(0) + "%" : "--"}</td>
            <td>${statusBadge}</td>
            <td style="font-size:11px;color:var(--text-muted);font-family:var(--font-main)">${formatDate(s.created_at)}</td>
        </tr>`;
    }).join("");
}

// =================== OPTİMİZASYON ===================

async function loadOptimization() {
    // Özet
    const summary = await apiFetch("/api/optimization/summary");
    if (summary) {
        document.getElementById("optCurrentWR").textContent = `%${summary.current_win_rate || 0}`;
        document.getElementById("optTargetWR").textContent = `%${summary.target_win_rate || 60}`;
        document.getElementById("optChangedParams").textContent = summary.total_optimizations || 0;

        // Değişen parametreler
        const paramsGrid = document.getElementById("changedParamsGrid");
        const changed = summary.changed_params || {};
        const keys = Object.keys(changed);

        if (keys.length === 0) {
            paramsGrid.innerHTML = '<div class="empty-state small"><p>Henüz parametre değişikliği yok</p></div>';
        } else {
            paramsGrid.innerHTML = keys.map(key => {
                const p = changed[key];
                return `<div class="param-card">
                    <div class="param-name">${key}</div>
                    <div class="param-values">
                        <span class="param-old">${formatParamValue(p.default)}</span>
                        <span class="param-arrow"><i class="fas fa-arrow-right"></i></span>
                        <span class="param-new">${formatParamValue(p.current)}</span>
                    </div>
                    <div class="param-change">Değişim: ${p.change_pct > 0 ? "+" : ""}${p.change_pct}%</div>
                </div>`;
            }).join("");
        }
    }

    // Loglar
    const logs = await apiFetch("/api/optimization/logs?limit=20");
    const logContainer = document.getElementById("optLogContainer");

    if (!logs || logs.length === 0) {
        logContainer.innerHTML = '<div class="empty-state small"><p>Optimizasyon günlüğü boş</p></div>';
        return;
    }

    logContainer.innerHTML = logs.map(log => {
        return `<div class="opt-log-item">
            <div class="opt-log-icon"><i class="fas fa-cog"></i></div>
            <div class="opt-log-content">
                <div class="opt-log-title">${log.param_name}</div>
                <div class="opt-log-detail">
                    ${formatParamValue(log.old_value)} → ${formatParamValue(log.new_value)} | ${log.reason || ""}
                </div>
            </div>
            <div class="opt-log-time">${formatDate(log.created_at)}</div>
        </div>`;
    }).join("");
}

async function runOptimization() {
    showToast("Optimizasyon çalıştırılıyor...", "info");
    const result = await apiPost("/api/optimization/run");
    if (result) {
        if (result.changes && result.changes.length > 0) {
            showToast(`${result.changes.length} parametre güncellendi!`, "success");
        } else {
            showToast(result.reason || "Değişiklik gerekli değil", "info");
        }
        loadOptimization();
    }
}

// =================== PERFORMANS ===================

async function loadPerformance() {
    const stats = await apiFetch("/api/performance");
    if (!stats) return;

    updateStats(stats);

    const grid = document.getElementById("performanceGrid");
    const compPerf = stats.component_performance || {};
    const keys = Object.keys(compPerf);

    if (keys.length === 0) {
        grid.innerHTML = `<div class="empty-state">
            <i class="fas fa-chart-pie"></i>
            <p>Yeterli veri toplandığında bileşen performansları burada görünecek</p>
        </div>`;
        return;
    }

    const compNames = {
        "MARKET_STRUCTURE": "Piyasa Yapısı",
        "ORDER_BLOCK": "Order Block",
        "FVG": "Fair Value Gap",
        "LIQUIDITY_SWEEP": "Likidite Taraması",
        "DISPLACEMENT": "Displacement",
        "OTE": "Optimal Trade Entry",
        "DISCOUNT_ZONE": "İndirim Bölgesi",
        "PREMIUM_ZONE": "Prim Bölgesi",
        "HTF_CONFIRMATION": "HTF Onayı"
    };

    const compIcons = {
        "MARKET_STRUCTURE": "fas fa-project-diagram",
        "ORDER_BLOCK": "fas fa-cube",
        "FVG": "fas fa-expand-arrows-alt",
        "LIQUIDITY_SWEEP": "fas fa-water",
        "DISPLACEMENT": "fas fa-bolt",
        "OTE": "fas fa-bullseye",
        "DISCOUNT_ZONE": "fas fa-tag",
        "PREMIUM_ZONE": "fas fa-gem",
        "HTF_CONFIRMATION": "fas fa-check-double"
    };

    grid.innerHTML = keys.map(key => {
        const comp = compPerf[key];
        const wr = comp.win_rate || 0;
        const wrClass = wr >= 60 ? "good" : wr >= 45 ? "medium" : "bad";
        const barColor = wr >= 60 ? "var(--accent-green)" : wr >= 45 ? "var(--accent-yellow)" : "var(--accent-red)";
        const icon = compIcons[key] || "fas fa-circle";
        const name = compNames[key] || key;

        return `<div class="perf-card">
            <div class="perf-card-header">
                <span class="perf-comp-name"><i class="${icon}" style="margin-right:6px;color:var(--accent-blue)"></i>${name}</span>
                <span class="perf-wr ${wrClass}">%${wr}</span>
            </div>
            <div class="perf-bar">
                <div class="perf-bar-fill" style="width:${wr}%;background:${barColor}"></div>
            </div>
            <div class="perf-stats">
                <span class="perf-stat-win"><i class="fas fa-check"></i> ${comp.wins} Kazanan</span>
                <span class="perf-stat-loss"><i class="fas fa-times"></i> ${comp.losses} Kaybeden</span>
                <span>Toplam: ${comp.total}</span>
            </div>
        </div>`;
    }).join("");
}

// =================== STATS GÜNCELLEME ===================

function updateStats(stats) {
    document.getElementById("statWinRate").textContent = `%${stats.win_rate || 0}`;
    document.getElementById("statTotalTrades").textContent = stats.total_trades || 0;
    document.getElementById("statActiveTrades").textContent = stats.active_trades || 0;

    const pnl = stats.total_pnl || 0;
    const pnlEl = document.getElementById("statTotalPnl");
    pnlEl.textContent = `${pnl >= 0 ? "+" : ""}${pnl}%`;
    pnlEl.style.color = pnl >= 0 ? "var(--accent-green)" : "var(--accent-red)";

    document.getElementById("statWatching").textContent = stats.watching_count || 0;
    document.getElementById("statAvgRR").textContent = stats.avg_rr || 0;
}

// =================== TAB NAVIGATION ===================

function switchTab(tabName) {
    // Tab butonları
    document.querySelectorAll(".tab-btn").forEach(btn => {
        btn.classList.toggle("active", btn.dataset.tab === tabName);
    });

    // Tab içerikleri
    document.querySelectorAll(".tab-content").forEach(content => {
        content.classList.toggle("active", content.id === `tab-${tabName}`);
    });

    // Tab verilerini yükle
    switch (tabName) {
        case "signals": loadActiveSignals(); break;
        case "coins": loadCoins(); break;
        case "watchlist": loadWatchlist(); break;
        case "history": loadHistory(); break;
        case "optimizer": loadOptimization(); break;
        case "performance": loadPerformance(); break;
    }
}

// =================== TOAST NOTIFICATIONS ===================

function showToast(message, type = "info") {
    const container = document.getElementById("toastContainer");
    const toast = document.createElement("div");
    toast.className = `toast ${type}`;

    const icons = {
        success: "fas fa-check-circle",
        error: "fas fa-exclamation-circle",
        info: "fas fa-info-circle",
        warning: "fas fa-exclamation-triangle"
    };

    toast.innerHTML = `<i class="${icons[type] || icons.info}"></i><span>${message}</span>`;
    container.appendChild(toast);

    toast.addEventListener("click", () => removeToast(toast));

    setTimeout(() => removeToast(toast), 5000);
}

function removeToast(toast) {
    toast.style.animation = "slideOutRight 0.3s ease";
    setTimeout(() => toast.remove(), 300);
}

// =================== HELPER FUNCTIONS ===================

function formatPrice(price) {
    if (!price && price !== 0) return "--";
    price = parseFloat(price);
    if (price >= 1000) return price.toFixed(2);
    if (price >= 1) return price.toFixed(4);
    if (price >= 0.01) return price.toFixed(6);
    return price.toFixed(8);
}

function formatTime(isoString) {
    if (!isoString) return "--";
    const d = new Date(isoString);
    return d.toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatDate(isoString) {
    if (!isoString) return "--";
    const d = new Date(isoString);
    return d.toLocaleDateString("tr-TR", {
        day: "2-digit", month: "2-digit", year: "numeric",
        hour: "2-digit", minute: "2-digit"
    });
}

function formatParamValue(val) {
    if (val === null || val === undefined) return "--";
    if (Number.isInteger(val)) return val.toString();
    return parseFloat(val).toFixed(4);
}

function updateServerTime() {
    const now = new Date();
    document.getElementById("serverTime").textContent =
        now.toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
