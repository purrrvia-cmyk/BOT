// =====================================================
// ICT Trading Bot v2.0 - Frontend JavaScript
// =====================================================

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
    const sym = data.symbol || "?";
    const dir = data.direction || "";
    const mode = data.entry_mode || "";
    const status = data.status || "";
    if (status === "REJECTED") return; // Reddedilen sinyalleri gösterme
    const statusLabel = status === "LIMIT_PLACED" ? "Limit Emir" : "Sinyal";
    showToast(
        `ICT ${statusLabel}: ${sym.split("-")[0]} ${dir} ${mode}`.trim(),
        status === "OPENED" ? "success" : "info"
    );
    loadActiveSignals();
    loadPerformance();
});

socket.on("watchlist_updated", (data) => {
    loadWatchlist();
});

socket.on("trade_closed", (data) => {
    const isWon = data.status === "WON";
    showToast(
        `ICT ${data.symbol} ${isWon ? "KAZANDI" : "KAYBETTİ"} | PnL: ${data.pnl_pct}%`,
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
    document.getElementById("lastScanTime").innerHTML =
        `<i class="fas fa-satellite-dish"></i> Son: ${formatTime(data.timestamp)}`;
    document.getElementById("scanCount").innerHTML =
        `<i class="fas fa-hashtag"></i> ${data.symbols_scanned} coin`;
});

socket.on("regime_update", (data) => {
    updateRegimeTopbar(data);
});

socket.on("optimization_done", (data) => {
    if (data.changes && data.changes.length > 0) {
        showToast(
            `ICT Optimizasyon: ${data.changes.length} parametre güncellendi`,
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
    updateInterval = setInterval(() => {
        if (botRunning) {
            loadActiveSignals();
            loadPerformance();
        }
        updateServerTime();
    }, 10000);
    updateServerTime();
    setInterval(updateServerTime, 1000);

    // Restore sidebar state
    const collapsed = localStorage.getItem("sidebarCollapsed") === "true";
    if (collapsed) {
        document.getElementById("sidebar").classList.add("collapsed");
    }
});

function loadAllData() {
    loadActiveSignals();
    loadCoins();
    loadWatchlist();
    loadHistory();
    loadPerformance();
    loadOptimization();
    loadBotStatus();
    loadRegime();
}

// =================== SIDEBAR ===================

function toggleSidebar() {
    const sidebar = document.getElementById("sidebar");
    const isMobile = window.innerWidth <= 768;

    if (isMobile) {
        sidebar.classList.toggle("mobile-open");
    } else {
        sidebar.classList.toggle("collapsed");
        localStorage.setItem("sidebarCollapsed", sidebar.classList.contains("collapsed"));
    }
}

// Close sidebar on mobile when clicking outside
document.addEventListener("click", (e) => {
    if (window.innerWidth <= 768) {
        const sidebar = document.getElementById("sidebar");
        const overlay = document.getElementById("sidebarOverlay");
        if (sidebar.classList.contains("mobile-open") && !sidebar.contains(e.target) && !e.target.closest(".mobile-menu-btn")) {
            sidebar.classList.remove("mobile-open");
        }
    }
});

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

// =================== BOT CONTROL ===================

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
            document.getElementById("lastScanTime").innerHTML =
                `<i class="fas fa-satellite-dish"></i> Son: ${formatTime(data.last_scan)}`;
        }
    }
}

function updateBotButton() {
    const btn = document.getElementById("btnToggleBot");
    if (botRunning) {
        btn.className = "bot-control-btn running";
        btn.innerHTML = '<span class="bot-btn-dot"></span><span class="bot-btn-text">Durdur</span>';
    } else {
        btn.className = "bot-control-btn stopped";
        btn.innerHTML = '<span class="bot-btn-dot"></span><span class="bot-btn-text">Başlat</span>';
    }
}

function updateConnectionStatus(connected) {
    const el = document.getElementById("connectionIndicator");
    if (connected) {
        el.innerHTML = '<span class="conn-dot connected"></span><span class="conn-text">Bağlı</span>';
    } else {
        el.innerHTML = '<span class="conn-dot disconnected"></span><span class="conn-text">Bağlantı kesildi</span>';
    }
}

// =================== TAB NAVIGATION ===================

const pageTitles = {
    signals: { icon: "fas fa-signal", text: "ICT Sinyalleri" },
    coins: { icon: "fas fa-coins", text: "Taranan Coinler" },
    watchlist: { icon: "fas fa-eye", text: "İzleme Listesi" },
    regime: { icon: "fas fa-globe", text: "Piyasa Rejimi" },
    history: { icon: "fas fa-clock-rotate-left", text: "Geçmiş İşlemler" },
    performance: { icon: "fas fa-chart-pie", text: "Performans" },
    optimizer: { icon: "fas fa-brain", text: "Optimizasyon" },

    forex: { icon: "fas fa-chart-line", text: "Forex & Altın ICT" },
    ame: { icon: "fas fa-atom", text: "AME Motor" },
    coindetail: { icon: "fas fa-magnifying-glass-chart", text: "Coin Detay Analizi" }
};

function switchTab(tabName) {
    // Nav items
    document.querySelectorAll(".nav-item").forEach(btn => {
        btn.classList.toggle("active", btn.dataset.tab === tabName);
    });

    // Tab contents
    document.querySelectorAll(".tab-content").forEach(content => {
        content.classList.toggle("active", content.id === `tab-${tabName}`);
    });

    // Update page title
    const titleInfo = pageTitles[tabName] || { icon: "fas fa-circle", text: tabName };
    document.getElementById("pageTitle").innerHTML = `<i class="${titleInfo.icon}"></i><span>${titleInfo.text}</span>`;

    // Coin detay sekmesinde stats-row gizle (daha fazla alan)
    const statsRow = document.getElementById("statsRow");
    if (statsRow) {
        statsRow.classList.toggle("hidden", tabName === "coindetail" || tabName === "forex" || tabName === "ame");
    }

    // Auto-refresh: coindetail'den çıkınca durdur
    if (tabName !== "coindetail" && autoRefreshActive) {
        stopAutoRefresh();
    }

    // Close sidebar on mobile
    if (window.innerWidth <= 768) {
        document.getElementById("sidebar").classList.remove("mobile-open");
    }

    // Reload data
    switch (tabName) {
        case "signals": loadActiveSignals(); break;
        case "coins": loadCoins(); break;
        case "watchlist": loadWatchlist(); break;
        case "regime": loadRegime(); break;
        case "history": loadHistory(); break;
        case "optimizer": loadOptimization(); break;
        case "performance": loadPerformance(); break;
        case "forex": initForexAutoScan(); break;
        case "ame": loadAME(); break;
    }
}

// =================== ACTIVE SIGNALS ===================

async function loadActiveSignals() {
    const data = await apiFetch("/api/signals/active");
    if (!data) return;

    const tbody = document.getElementById("activeSignalsTable");
    const stats = await apiFetch("/api/performance");
    if (stats) updateStats(stats);

    // Update nav badge
    const badge = document.getElementById("navBadgeSignals");
    if (data.length > 0) {
        badge.textContent = data.length;
    } else {
        badge.textContent = "";
    }

    if (data.length === 0) {
        tbody.innerHTML = `
            <tr class="empty-row"><td colspan="11">
                <div class="empty-state">
                    <div class="empty-icon"><i class="fas fa-inbox"></i></div>
                    <p class="empty-title">Henüz aktif sinyal yok</p>
                    <p class="empty-desc">Bot başlatıldığında sinyaller burada görünecek</p>
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

        return `<tr ondblclick="openICTChart('${s.symbol}')" class="signal-row-clickable" title="Çift tıkla → ICT Chart">
            <td class="td-muted">#${s.id}</td>
            <td>${coinDisplay}</td>
            <td>${dirBadge}</td>
            <td>${formatPrice(s.entry_price)}</td>
            <td>${s.current_price ? formatPrice(s.current_price) : "--"}</td>
            <td class="td-sl">${formatPrice(s.stop_loss)}</td>
            <td class="td-tp">${formatPrice(s.take_profit)}</td>
            <td>
                <div class="confidence-bar">
                    <div class="confidence-track">
                        <div class="confidence-fill ${confClass}" style="width:${confidence}%"></div>
                    </div>
                    <span class="td-conf">${confidence}%</span>
                </div>
            </td>
            <td><span class="${pnlClass}">${pnlText}</span></td>
            <td>${statusBadge}</td>
            <td>
                <button class="btn-cancel" onclick="cancelSignal(${s.id})">
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

// =================== COINS ===================

let allCoinsData = [];

async function loadCoins() {
    const data = await apiFetch("/api/coins");
    if (!data) return;

    allCoinsData = data.coins || [];
    document.getElementById("activeCoinCount").textContent = data.total_coins || 0;

    // Nav badge
    const badge = document.getElementById("navBadgeCoins");
    if (allCoinsData.length > 0) {
        badge.textContent = allCoinsData.length;
    } else {
        badge.textContent = "";
    }

    renderCoinsTable(allCoinsData);
}

function filterCoins() {
    const query = document.getElementById("coinSearchInput").value.toUpperCase().trim();
    let filtered = allCoinsData;
    if (query) {
        filtered = filtered.filter(c => c.symbol.toUpperCase().includes(query));
    }
    renderCoinsTable(filtered);
}

function renderCoinsTable(coins) {
    const tbody = document.getElementById("coinsTable");

    if (!coins || coins.length === 0) {
        tbody.innerHTML = `
            <tr class="empty-row"><td colspan="6">
                <div class="empty-state">
                    <div class="empty-icon"><i class="fas fa-satellite-dish"></i></div>
                    <p class="empty-title">Coin bulunamadı</p>
                    <p class="empty-desc">OKX'ten yüksek hacimli coinler yükleniyor...</p>
                </div>
            </td></tr>`;
        return;
    }

    const maxVol = coins[0]?.volume_usdt || 1;
    tbody.innerHTML = coins.map((coin, index) => {
        const parts = coin.symbol.split("-");
        const coinName = parts[0] || coin.symbol;
        const pairName = parts[1] || "USDT";

        const price = coin.last_price || 0;
        const change = coin.change_pct || 0;
        const changeClass = change >= 0 ? "pnl-positive" : "pnl-negative";
        const changeIcon = change >= 0 ? "fa-caret-up" : "fa-caret-down";
        const changeText = change >= 0 ? `+${change.toFixed(2)}%` : `${change.toFixed(2)}%`;

        const volume = coin.volume_usdt || 0;
        const volText = volume >= 1_000_000_000
            ? `$${(volume / 1_000_000_000).toFixed(2)}B`
            : volume >= 1_000_000
            ? `$${(volume / 1_000_000).toFixed(2)}M`
            : `$${volume.toLocaleString("tr-TR")}`;
        const volBarPct = Math.min(100, (volume / maxVol) * 100);

        return `<tr class="coin-row" onclick="openCoinDetail('${coin.symbol}')" title="Detaylı Teknik Analiz">
            <td class="col-rank">${index + 1}</td>
            <td class="col-coin">
                <div class="coin-cell">
                    <div class="coin-avatar">${coinName.substring(0, 2)}</div>
                    <div class="coin-symbol">
                        <span class="coin-name">${coinName}</span>
                        <span class="coin-pair">${pairName}</span>
                    </div>
                </div>
            </td>
            <td class="col-price">${formatPrice(price)}</td>
            <td class="col-change"><span class="${changeClass}"><i class="fas ${changeIcon}"></i>${changeText}</span></td>
            <td class="col-volume">
                <div class="volume-cell">
                    <span class="vol-text">${volText}</span>
                    <div class="vol-track"><div class="vol-fill" style="width:${volBarPct}%"></div></div>
                </div>
            </td>
            <td class="col-action">
                <button class="btn-analyze" onclick="event.stopPropagation(); openCoinDetail('${coin.symbol}')">
                    <i class="fas fa-magnifying-glass-chart"></i>
                </button>
            </td>
        </tr>`;
    }).join("");
}

// analyzeSymbol replaced by openCoinDetail modal

// =================== WATCHLIST ===================

async function loadWatchlist() {
    const ictData = await apiFetch("/api/watchlist");
    const allItems = (ictData || []).map(i => ({ ...i, _strategy: "ICT" }));

    const tbody = document.getElementById("watchlistTable");

    // Nav badge
    const badge = document.getElementById("navBadgeWatch");
    badge.textContent = allItems.length > 0 ? allItems.length : "";

    if (allItems.length === 0) {
        tbody.innerHTML = `
            <tr class="empty-row"><td colspan="9">
                <div class="empty-state">
                    <div class="empty-icon"><i class="fas fa-binoculars"></i></div>
                    <p class="empty-title">İzleme listesi boş</p>
                    <p class="empty-desc">Potansiyel sinyaller burada bekletilir</p>
                </div>
            </td></tr>`;
        return;
    }

    tbody.innerHTML = allItems.map(item => {
        const stratBadge = '<span class="badge badge-ict"><i class="fas fa-signal"></i> ICT</span>';

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
            <td>${stratBadge}</td>
            <td>${coinDisplay}</td>
            <td>${dirBadge}</td>
            <td>${formatPrice(item.potential_entry)}</td>
            <td>${item.initial_score ? item.initial_score.toFixed(1) : "--"}%</td>
            <td>${item.current_score ? item.current_score.toFixed(1) : "--"}% ${scoreChangeText}</td>
            <td>
                <div class="confidence-bar">
                    <div class="confidence-track" style="width:60px">
                        <div class="confidence-fill medium" style="width:${progressPct}%"></div>
                    </div>
                    <span class="watch-progress">${item.candles_watched}/${item.max_watch_candles}</span>
                </div>
            </td>
            <td class="watch-reason">${item.watch_reason || "--"}</td>
            <td><span class="badge badge-watching"><i class="fas fa-eye"></i> İzleniyor</span></td>
        </tr>`;
    }).join("");

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

// =================== HISTORY ===================

async function loadHistory() {
    const data = await apiFetch("/api/signals/history?limit=50");
    if (!data) return;

    const tbody = document.getElementById("historyTable");
    const completed = data.filter(s => s.status === "WON" || s.status === "LOST");
    const wins = completed.filter(s => s.status === "WON").length;
    const losses = completed.filter(s => s.status === "LOST").length;

    document.getElementById("histWins").textContent = wins;
    document.getElementById("histLosses").textContent = losses;

    // Sadece tamamlanmış işlemleri göster (backend zaten filtreli döndürüyor, ek güvenlik)
    const historyItems = data.filter(s => ["WON", "LOST", "CANCELLED"].includes(s.status));

    if (historyItems.length === 0) {
        tbody.innerHTML = `
            <tr class="empty-row"><td colspan="11">
                <div class="empty-state">
                    <div class="empty-icon"><i class="fas fa-archive"></i></div>
                    <p class="empty-title">Geçmiş işlem yok</p>
                    <p class="empty-desc">Tamamlanan işlemler burada görünecek</p>
                </div>
            </td></tr>`;
        return;
    }

    tbody.innerHTML = historyItems.map(s => {
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
        }

        const pnl = s.pnl_pct || 0;
        const pnlClass = pnl >= 0 ? "pnl-positive" : "pnl-negative";
        const pnlText = pnl !== 0 ? (pnl >= 0 ? `+${pnl.toFixed(2)}%` : `${pnl.toFixed(2)}%`) : "--";

        const coinParts = s.symbol.split("-");
        const coinDisplay = `<div class="coin-symbol"><span class="coin-name">${coinParts[0]}</span><span class="coin-pair">/${coinParts[1]}</span></div>`;

        return `<tr>
            <td class="td-muted">#${s.id}</td>
            <td>${coinDisplay}</td>
            <td>${dirBadge}</td>
            <td>${formatPrice(s.entry_price)}</td>
            <td>${s.close_price ? formatPrice(s.close_price) : "--"}</td>
            <td class="td-sl">${formatPrice(s.stop_loss)}</td>
            <td class="td-tp">${formatPrice(s.take_profit)}</td>
            <td><span class="${pnlClass}">${pnlText}</span></td>
            <td>${s.confidence ? s.confidence.toFixed(0) + "%" : "--"}</td>
            <td>${statusBadge}</td>
            <td class="td-date">${formatDate(s.created_at)}</td>
        </tr>`;
    }).join("");
}

// =================== OPTIMIZATION ===================

async function loadOptimization() {
    const summary = await apiFetch("/api/optimization/summary");
    if (summary) {
        document.getElementById("optCurrentWR").textContent = `%${summary.current_win_rate || 0}`;
        document.getElementById("optTargetWR").textContent = `%${summary.target_win_rate || 60}`;
        document.getElementById("optChangedParams").textContent = summary.total_optimizations || 0;

        // Son optimizasyon tarihi
        const lastOptEl = document.getElementById("optLastDate");
        if (lastOptEl) {
            if (summary.last_optimization) {
                lastOptEl.textContent = formatDate(summary.last_optimization);
            } else {
                lastOptEl.textContent = "Henüz çalışmadı";
            }
        }

        // İşlem sayısı bilgisi
        const tradeInfoEl = document.getElementById("optTradeInfo");
        if (tradeInfoEl && summary.performance) {
            const p = summary.performance;
            const minNeeded = 5;
            if (p.total_trades < minNeeded) {
                tradeInfoEl.innerHTML = `<span class="td-sl"><i class="fas fa-exclamation-triangle"></i> ${p.total_trades}/${minNeeded} tamamlanmış işlem (${minNeeded - p.total_trades} daha gerekli)</span>`;
            } else {
                tradeInfoEl.innerHTML = `<span class="td-tp"><i class="fas fa-check-circle"></i> ${p.total_trades} tamamlanmış işlem — optimizer aktif</span>`;
            }
        }

        const paramsGrid = document.getElementById("changedParamsGrid");
        const changed = summary.changed_params || {};
        const keys = Object.keys(changed);

        if (keys.length === 0) {
            paramsGrid.innerHTML = '<div class="empty-state small"><p class="empty-title">Henüz parametre değişikliği yok</p></div>';
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

    const logs = await apiFetch("/api/optimization/logs?limit=20");
    const logContainer = document.getElementById("optLogContainer");

    if (!logs || logs.length === 0) {
        logContainer.innerHTML = '<div class="empty-state small"><p class="empty-title">Günlük boş</p></div>';
        return;
    }

    logContainer.innerHTML = logs.map(log => {
        return `<div class="opt-log-item">
            <div class="opt-log-icon"><i class="fas fa-gear"></i></div>
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
    showToast("Optimizasyon çalıştırılıyor... (tarama bitimini bekliyorum)", "info");
    try {
        const resp = await fetch("/api/optimization/run", { method: "POST" });
        const result = await resp.json();

        if (resp.status === 409) {
            showToast(result.reason || "Tarama devam ediyor, tekrar deneyin", "warning");
        } else if (resp.status === 500) {
            showToast(`Hata: ${result.reason || "Bilinmeyen hata"}`, "error");
        } else if (result.status === "SKIPPED") {
            showToast(result.reason || "Yeterli işlem yok", "warning");
        } else if (result.changes && result.changes.length > 0) {
            showToast(`${result.changes.length} parametre güncellendi!`, "success");
        } else {
            showToast("Optimizasyon tamamlandı — değişiklik gerekli değil", "info");
        }
        loadOptimization();
    } catch (err) {
        showToast("Optimizasyon isteği başarısız: " + err.message, "error");
    }
}

// =================== PERFORMANCE ===================

async function loadPerformance() {
    const stats = await apiFetch("/api/performance");
    if (!stats) return;

    updateStats(stats);

    const grid = document.getElementById("performanceGrid");
    const compPerf = stats.component_performance || {};
    const keys = Object.keys(compPerf);

    if (keys.length === 0) {
        grid.innerHTML = `<div class="empty-state">
            <div class="empty-icon"><i class="fas fa-chart-pie"></i></div>
            <p class="empty-title">Yeterli veri bekleniyor</p>
            <p class="empty-desc">İşlem tamamlandıkça bileşen performansları görünecek</p>
        </div>`;
    } else {
        renderPerfGrid(grid, compPerf, "ict");
    }

}

function renderPerfGrid(grid, compPerf, type) {
    const keys = Object.keys(compPerf);

    if (keys.length === 0) {
        grid.innerHTML = `<div class="empty-state">
            <div class="empty-icon"><i class="fas fa-chart-pie"></i></div>
            <p class="empty-title">Yeterli veri bekleniyor</p>
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
        "HTF_CONFIRMATION": "HTF Onayı",
    };

    const compIcons = {
        "MARKET_STRUCTURE": "fas fa-project-diagram",
        "ORDER_BLOCK": "fas fa-cube",
        "FVG": "fas fa-expand",
        "LIQUIDITY_SWEEP": "fas fa-water",
        "DISPLACEMENT": "fas fa-bolt",
        "OTE": "fas fa-bullseye",
        "DISCOUNT_ZONE": "fas fa-tag",
        "PREMIUM_ZONE": "fas fa-gem",
        "HTF_CONFIRMATION": "fas fa-check-double",
    };

    const accentColor = "var(--blue)";

    grid.innerHTML = keys.map(key => {
        const comp = compPerf[key];
        const wr = comp.win_rate || 0;
        const wrClass = wr >= 60 ? "good" : wr >= 45 ? "medium" : "bad";
        const barColor = wr >= 60 ? "var(--green)" : wr >= 45 ? "var(--yellow)" : "var(--red)";
        const icon = compIcons[key] || "fas fa-circle";
        const name = compNames[key] || key;

        return `<div class="perf-card">
            <div class="perf-card-header">
                <span class="perf-comp-name"><i class="${icon}" style="margin-right:6px;color:${accentColor}"></i>${name}</span>
                <span class="perf-wr ${wrClass}">%${wr}</span>
            </div>
            <div class="perf-bar">
                <div class="perf-bar-fill" style="width:${wr}%;background:${barColor}"></div>
            </div>
            <div class="perf-stats">
                <span class="perf-stat-win"><i class="fas fa-check"></i> ${comp.wins} Win</span>
                <span class="perf-stat-loss"><i class="fas fa-times"></i> ${comp.losses} Loss</span>
                <span>Total: ${comp.total}</span>
            </div>
        </div>`;
    }).join("");
}

// =================== MARKET REGIME ===================

const REGIME_META = {
    RISK_ON:       { label: "Risk-On", emoji: "🟢", color: "var(--green)", bg: "rgba(34, 197, 94, 0.12)", border: "rgba(34, 197, 94, 0.3)" },
    ALT_SEASON:    { label: "Alt Season", emoji: "🚀", color: "var(--purple)", bg: "rgba(168, 85, 247, 0.12)", border: "rgba(168, 85, 247, 0.3)" },
    RISK_OFF:      { label: "Risk-Off", emoji: "🔴", color: "var(--red)", bg: "rgba(239, 68, 68, 0.12)", border: "rgba(239, 68, 68, 0.3)" },
    CAPITULATION:  { label: "Kapitülasyon", emoji: "☠️", color: "#f97316", bg: "rgba(249, 115, 22, 0.12)", border: "rgba(249, 115, 22, 0.3)" },
    NEUTRAL:       { label: "Nötr", emoji: "⚪", color: "var(--text-secondary)", bg: "rgba(148, 163, 184, 0.1)", border: "rgba(148, 163, 184, 0.2)" },
    UNKNOWN:       { label: "Bilinmiyor", emoji: "❓", color: "var(--text-muted)", bg: "rgba(100,100,100,0.1)", border: "rgba(100,100,100,0.2)" }
};

const FLOW_LABELS = {
    INFLOW: { text: "Para Girişi", icon: "fa-arrow-trend-up", color: "var(--green)" },
    OUTFLOW: { text: "Para Çıkışı", icon: "fa-arrow-trend-down", color: "var(--red)" },
    PANIC_SELL: { text: "Panik Satış", icon: "fa-triangle-exclamation", color: "#f97316" },
    NEUTRAL: { text: "Nötr", icon: "fa-minus", color: "var(--text-secondary)" }
};

const BTCD_LABELS = {
    RISING: { text: "Yükseliş (BTC Güçlü)", color: "#f59e0b" },
    FALLING: { text: "Düşüş (Altlar Güçlü)", color: "var(--green)" },
    NEUTRAL: { text: "Stabil", color: "var(--text-secondary)" }
};

function updateRegimeTopbar(data) {
    const meta = REGIME_META[data.regime] || REGIME_META.UNKNOWN;
    const dot = document.getElementById("regimeDot");
    const label = document.getElementById("regimeLabel");
    const bias = document.getElementById("regimeBias");

    dot.style.background = meta.color;
    dot.style.boxShadow = `0 0 8px ${meta.color}`;
    label.textContent = `${meta.emoji} ${meta.label}`;
    label.style.color = meta.color;

    const btcBias = data.btc_bias || "NEUTRAL";
    if (btcBias === "LONG") {
        bias.innerHTML = `<i class="fas fa-arrow-up"></i> BTC`;
        bias.style.color = "var(--green)";
    } else if (btcBias === "SHORT") {
        bias.innerHTML = `<i class="fas fa-arrow-down"></i> BTC`;
        bias.style.color = "var(--red)";
    } else {
        bias.innerHTML = `<i class="fas fa-minus"></i> BTC`;
        bias.style.color = "var(--text-muted)";
    }
}

async function loadRegime() {
    const data = await apiFetch("/api/regime");
    if (!data) return;

    const isUnknown = data.regime === "UNKNOWN";

    updateRegimeTopbar(data);

    // ═══ HERO BAR: Rejim + Fear&Greed ═══
    const meta = REGIME_META[data.regime] || REGIME_META.UNKNOWN;
    const mainIcon = document.getElementById("regimeMainIcon");
    mainIcon.style.background = meta.bg;
    mainIcon.style.color = meta.color;
    mainIcon.style.border = `1px solid ${meta.border}`;
    document.getElementById("regimeMainValue").textContent = `${meta.emoji} ${meta.label}`;
    document.getElementById("regimeMainValue").style.color = meta.color;

    // Fear & Greed Gauge
    const fg = data.fear_greed || {};
    const fgScore = fg.score ?? 50;
    const fgFill = document.getElementById("fgFill");
    if (fgFill) fgFill.style.left = `${fgScore}%`;
    const fgLabelEl = document.getElementById("fgLabel");
    if (fgLabelEl) {
        fgLabelEl.textContent = fg.label || "Nötr";
        fgLabelEl.style.color = fg.color || "var(--text-muted)";
    }
    const fgScoreEl = document.getElementById("fgScore");
    if (fgScoreEl) {
        fgScoreEl.textContent = fgScore;
        fgScoreEl.style.color = fg.color || "var(--text-secondary)";
    }

    // ═══ HIZLI GÖSTERGE KARTLARI ═══

    // BTC Trend
    const btcDetails = data.btc_details || {};
    const btcBias = btcDetails.bias || "NEUTRAL";
    const btcEl = document.getElementById("regimeBtcTrend");
    if (isUnknown || btcBias === "UNKNOWN") {
        btcEl.innerHTML = `<i class="fas fa-clock"></i> Veri Bekleniyor`;
        btcEl.style.color = "var(--text-muted)";
    } else if (btcBias === "LONG") {
        const score = btcDetails.trend_score ? ` (${btcDetails.trend_score > 0 ? '+' : ''}${btcDetails.trend_score})` : '';
        btcEl.innerHTML = `<i class="fas fa-arrow-up"></i> Yükseliyor${score}`;
        btcEl.style.color = "var(--green)";
    } else if (btcBias === "SHORT") {
        const score = btcDetails.trend_score ? ` (${btcDetails.trend_score})` : '';
        btcEl.innerHTML = `<i class="fas fa-arrow-down"></i> Düşüyor${score}`;
        btcEl.style.color = "var(--red)";
    } else {
        const score = btcDetails.trend_score ? ` (${btcDetails.trend_score > 0 ? '+' : ''}${btcDetails.trend_score})` : '';
        btcEl.innerHTML = `<i class="fas fa-minus"></i> Yatay${score}`;
        btcEl.style.color = "var(--text-muted)";
    }

    // Para Akışı
    const flow = data.usdt_flow || {};
    const flowEl = document.getElementById("regimeFlow");
    if (isUnknown || flow.direction === "UNKNOWN") {
        flowEl.innerHTML = `<i class="fas fa-clock"></i> Veri Bekleniyor`;
        flowEl.style.color = "var(--text-muted)";
    } else {
        const flowMeta = FLOW_LABELS[flow.direction] || FLOW_LABELS.NEUTRAL;
        const volPct = flow.volume_change_pct ? ` (${flow.volume_change_pct > 0 ? '+' : ''}${flow.volume_change_pct}%)` : '';
        flowEl.innerHTML = `<i class="fas ${flowMeta.icon}"></i> ${flowMeta.text}${volPct}`;
        flowEl.style.color = flowMeta.color;
    }

    // BTC.D
    const btcD = data.btc_dominance || {};
    const btcDEl = document.getElementById("regimeBtcD");
    if (isUnknown || btcD.direction === "UNKNOWN") {
        btcDEl.textContent = "Veri Bekleniyor";
        btcDEl.style.color = "var(--text-muted)";
    } else {
        const btcdMeta = BTCD_LABELS[btcD.direction] || BTCD_LABELS.NEUTRAL;
        btcDEl.textContent = btcdMeta.text;
        btcDEl.style.color = btcdMeta.color;
    }

    // Volatilite
    const vol = data.volatility || {};
    const volEl = document.getElementById("regimeVolatility");
    if (volEl) {
        const volMeta = {
            HIGH: { text: `Yüksek (ATR x${vol.atr_ratio || "?"})`, icon: "fa-bolt", color: "#f97316" },
            LOW: { text: `Düşük — Sıkışma (ATR x${vol.atr_ratio || "?"})`, icon: "fa-compress", color: "var(--yellow, #f0ad4e)" },
            NORMAL: { text: `Normal (ATR x${vol.atr_ratio || "1.0"})`, icon: "fa-wave-square", color: "var(--text-secondary)" },
        };
        const vm = volMeta[vol.state] || volMeta.NORMAL;
        volEl.innerHTML = `<i class="fas ${vm.icon}"></i> ${vm.text}`;
        volEl.style.color = vm.color;
    }

    // Piyasa Genişliği
    const altHealth = data.altcoin_health || {};
    const breadthEl = document.getElementById("regimeBreadth");
    if (breadthEl) {
        const greenR = altHealth.green_ratio ?? 50;
        const breadthMeta = {
            STRONG_BULLISH: { text: `%${greenR} Yeşil — Güçlü Ralli`, color: "var(--green)" },
            BULLISH: { text: `%${greenR} Yeşil — Sağlıklı`, color: "var(--green)" },
            STRONG_BEARISH: { text: `%${greenR} Yeşil — Yaygın Düşüş`, color: "var(--red)" },
            BEARISH: { text: `%${greenR} Yeşil — Baskı Altında`, color: "var(--red)" },
            NEUTRAL: { text: `%${greenR} Yeşil — Karışık`, color: "var(--text-secondary)" },
        };
        const bm = breadthMeta[altHealth.market_breadth] || breadthMeta.NEUTRAL;
        breadthEl.textContent = bm.text;
        breadthEl.style.color = bm.color;
    }

    // ═══ ALTCOİN ENDEKS BARI ═══
    const setAltIdx = (id, val, suffix = "%") => {
        const el = document.getElementById(id);
        if (!el) return;
        const v = parseFloat(val) || 0;
        el.textContent = `${v >= 0 ? "+" : ""}${v.toFixed(2)}${suffix}`;
        el.style.color = v > 0.3 ? "var(--green)" : v < -0.3 ? "var(--red)" : "var(--text-secondary)";
    };
    setAltIdx("altTotal2", altHealth.total2_proxy);
    setAltIdx("altTotal3", altHealth.total3_proxy);
    setAltIdx("altOthers", altHealth.others_proxy);
    setAltIdx("altAvgChange", altHealth.avg_change_1h);

    const greenRatioEl = document.getElementById("altGreenRatio");
    if (greenRatioEl) {
        const gr = altHealth.green_ratio ?? 50;
        greenRatioEl.textContent = `%${gr}`;
        greenRatioEl.style.color = gr >= 60 ? "var(--green)" : gr <= 40 ? "var(--red)" : "var(--text-secondary)";
    }

    // ═══ PİYASA YORUMU ═══
    const commentary = data.market_commentary || [];
    const commentaryEl = document.getElementById("marketCommentary");
    if (commentaryEl) {
        if (commentary.length === 0) {
            commentaryEl.innerHTML = `<div class="empty-state">
                <div class="empty-icon"><i class="fas fa-newspaper"></i></div>
                <p class="empty-title">Piyasa yorumu bekleniyor</p>
                <p class="empty-desc">Bot çalıştığında detaylı piyasa analizi burada görünecek</p>
            </div>`;
        } else {
            const iconColors = {
                "fa-globe": "#6366f1",
                "fab fa-bitcoin": "#f7931a",
                "fa-money-bill-transfer": "#22c55e",
                "fa-coins": "#a855f7",
                "fa-shield-halved": "#f97316",
                "fa-face-smile": "#3b82f6",
                "fa-chess": "#14b8a6",
            };
            commentaryEl.innerHTML = commentary.map(section => {
                const isFab = section.icon.startsWith("fab ");
                const iconClass = isFab ? section.icon : `fas ${section.icon}`;
                const iconColor = iconColors[section.icon] || "#818cf8";
                return `<div class="commentary-section">
                    <div class="commentary-icon" style="background:${iconColor}15;color:${iconColor}">
                        <i class="${iconClass}"></i>
                    </div>
                    <div class="commentary-body">
                        <div class="commentary-title">${section.title}</div>
                        <div class="commentary-text">${section.text}</div>
                    </div>
                </div>`;
            }).join("");
        }
    }

    // ═══ FIRSAT LİSTESİ ═══
    const longList = (data.long_candidates || []).map(s => s.split("-")[0]);
    const shortList = (data.short_candidates || []).map(s => s.split("-")[0]);
    document.getElementById("regimeLongList").textContent = longList.length > 0 ? longList.join(", ") : "Yok";
    document.getElementById("regimeShortList").textContent = shortList.length > 0 ? shortList.join(", ") : "Yok";

    // ═══ RS SIRALAMASSI TABLOSU ═══
    const rankings = data.rs_rankings || [];
    const tbody = document.getElementById("rsRankingsTable");

    if (rankings.length === 0) {
        tbody.innerHTML = `<tr class="empty-row"><td colspan="7">
            <div class="empty-state">
                <div class="empty-icon"><i class="fas fa-globe"></i></div>
                <p class="empty-title">Rejim verisi bekleniyor</p>
                <p class="empty-desc">Bot çalıştığında piyasa analizi burada görünecek</p>
            </div>
        </td></tr>`;
        return;
    }

    tbody.innerHTML = rankings.map((coin, idx) => {
        const parts = coin.symbol.split("-");
        const coinName = `<div class="coin-symbol"><span class="coin-name">${parts[0]}</span><span class="coin-pair">/${parts[1]}</span></div>`;

        const rs = coin.rs_score;
        const rsClass = rs > 1 ? "pnl-positive" : rs < -1 ? "pnl-negative" : "";
        const rsBar = Math.min(Math.abs(rs) * 20, 100);
        const rsColor = rs > 0 ? "var(--green)" : "var(--red)";

        const chg = coin.price_change_1h || 0;
        const chgClass = chg >= 0 ? "pnl-positive" : "pnl-negative";
        const chgText = chg >= 0 ? `+${chg.toFixed(2)}%` : `${chg.toFixed(2)}%`;

        const cvol = coin.vol_ratio || 0;
        const volColor = cvol >= 1.5 ? "var(--green)" : cvol >= 0.8 ? "var(--text-primary)" : "var(--red)";

        const strs = coin.short_term_rs || 0;
        const strsClass = strs > 0 ? "pnl-positive" : strs < 0 ? "pnl-negative" : "";

        // Durum badge
        const isLong = longList.includes(parts[0]);
        const isShort = shortList.includes(parts[0]);
        let statusBadge = '<span class="badge" style="background:rgba(100,100,100,0.2);color:var(--text-muted)">Nötr</span>';
        if (isLong) statusBadge = '<span class="badge badge-long"><i class="fas fa-arrow-up"></i> LONG Aday</span>';
        if (isShort) statusBadge = '<span class="badge badge-short"><i class="fas fa-arrow-down"></i> SHORT Aday</span>';

        return `<tr>
            <td class="td-muted">${idx + 1}</td>
            <td>${coinName}</td>
            <td>
                <div class="rs-bar-wrap">
                    <div class="rs-bar-track">
                        <div class="rs-bar-fill" style="width:${rsBar}%;background:${rsColor}"></div>
                    </div>
                    <span class="${rsClass} rs-score">${rs > 0 ? "+" : ""}${rs.toFixed(2)}</span>
                </div>
            </td>
            <td><span class="${chgClass}">${chgText}</span></td>
            <td class="rs-score" style="color:${volColor}">${cvol.toFixed(2)}x</td>
            <td><span class="${strsClass}">${strs > 0 ? "+" : ""}${strs.toFixed(2)}</span></td>
            <td>${statusBadge}</td>
        </tr>`;
    }).join("");
}

async function refreshRegimeManual() {
    showToast("Rejim analizi başlatılıyor...", "info");
    try {
        const resp = await fetch("/api/regime/refresh", { method: "POST" });
        const data = await resp.json();
        if (data.error) {
            showToast(`Rejim hatası: ${data.error}`, "error");
            return;
        }
        showToast("Rejim analizi tamamlandı!", "success");
        loadRegime();
    } catch (e) {
        showToast("Rejim analizi başarısız", "error");
    }
}

// =================== STATS UPDATE ===================

function updateStats(stats) {
    document.getElementById("statWinRate").textContent = `%${stats.win_rate || 0}`;
    document.getElementById("statTotalTrades").textContent = stats.total_trades || 0;
    document.getElementById("statActiveTrades").textContent = stats.active_trades || 0;

    const pnl = stats.total_pnl || 0;
    const pnlEl = document.getElementById("statTotalPnl");
    pnlEl.textContent = `${pnl >= 0 ? "+" : ""}${pnl}%`;
    pnlEl.style.color = pnl >= 0 ? "var(--green)" : "var(--red)";

    document.getElementById("statWatching").textContent = stats.watching_count || 0;
    document.getElementById("statAvgRR").textContent = stats.avg_rr || 0;
}

// =================== TOAST NOTIFICATIONS ===================

function showToast(message, type = "info") {
    const container = document.getElementById("toastContainer");
    const toast = document.createElement("div");
    toast.className = `toast ${type}`;

    const icons = {
        success: "fas fa-check-circle",
        error: "fas fa-circle-exclamation",
        info: "fas fa-circle-info",
        warning: "fas fa-triangle-exclamation"
    };

    toast.innerHTML = `<i class="${icons[type] || icons.info}"></i><span>${message}</span>`;
    container.appendChild(toast);

    toast.addEventListener("click", () => removeToast(toast));
    setTimeout(() => removeToast(toast), 5000);
}

function removeToast(toast) {
    toast.style.animation = "toastSlideOut 0.3s ease";
    setTimeout(() => toast.remove(), 300);
}

// =================== HELPERS ===================

async function refreshWithFeedback(btn, loadFn) {
    if (btn.disabled) return;
    btn.disabled = true;
    btn.classList.add("refreshing");
    try {
        await loadFn();
    } finally {
        btn.classList.remove("refreshing");
        btn.disabled = false;
    }
}

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

// =================== COIN DETAIL TAB ===================

let coinDetailData = null;
let currentModalTf = "15m";

async function openCoinDetail(symbol) {
    // Switch to coindetail tab
    switchTab('coindetail');

    // Reset state
    document.getElementById("modalLoading").style.display = "flex";
    document.getElementById("tfSection").style.display = "none";
    document.getElementById("orderbookSection").style.display = "none";
    document.getElementById("verdictScoreBar").style.display = "none";
    document.getElementById("verdictWarnings").innerHTML = "";
    document.getElementById("marketDataSection").style.display = "none";

    const parts = symbol.split("-");
    document.getElementById("modalCoinName").textContent = `${parts[0]}/${parts[1] || "USDT"}`;
    document.getElementById("modalCoinPrice").textContent = "Yükleniyor...";

    const verdictBar = document.getElementById("modalOverallVerdict");
    verdictBar.className = "detail-verdict-card";
    document.getElementById("verdictLabel").textContent = "Gelişmiş analiz hesaplanıyor...";
    document.getElementById("verdictDesc").textContent = "";

    // Update page title with coin name
    document.getElementById("pageTitle").innerHTML = `<i class="fas fa-magnifying-glass-chart"></i><span>${parts[0]} Detay Analizi</span>`;

    // Fetch data
    const data = await apiFetch(`/api/coin-detail/${symbol}`);
    document.getElementById("modalLoading").style.display = "none";

    if (!data || data.error) {
        document.getElementById("verdictLabel").textContent = "Analiz başarısız";
        document.getElementById("verdictDesc").textContent = data?.error || "Veri alınamadı";
        return;
    }

    coinDetailData = data;
    coinDetailData._symbol = symbol;
    currentModalTf = "15m";

    // Price info
    const price = data.price || {};
    const change = price.change24h || 0;
    const changeStr = change >= 0 ? `+${change.toFixed(2)}%` : `${change.toFixed(2)}%`;
    const changeColor = change >= 0 ? "var(--green)" : "var(--red)";
    document.getElementById("modalCoinPrice").innerHTML =
        `${formatPrice(price.last)} <span style="color:${changeColor};font-size:12px;font-weight:600;margin-left:8px">${changeStr}</span>` +
        `<span style="color:var(--text-muted);margin-left:10px;font-size:11px">24h H: ${formatPrice(price.high24h)} / L: ${formatPrice(price.low24h)}</span>`;

    // Overall verdict
    const ov = data.overall || {};
    const verdictType = ov.verdict || "NEUTRAL";
    verdictBar.className = `detail-verdict-card verdict-${verdictType}`;
    document.getElementById("verdictLabel").textContent = `🤖 ${ov.label || "Bilinmiyor"}`;
    document.getElementById("verdictDesc").innerHTML = (ov.description || "").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\n/g, "<br>");

    // Score bar
    const scoreBar = document.getElementById("verdictScoreBar");
    scoreBar.style.display = "block";
    const bullTotal = ov.bull_total || 0;
    const bearTotal = ov.bear_total || 0;
    const maxScore = 100;
    document.getElementById("scoreFillBull").style.width = `${Math.min(bullTotal / maxScore * 50, 50)}%`;
    document.getElementById("scoreFillBear").style.width = `${Math.min(bearTotal / maxScore * 50, 50)}%`;
    const netScore = ov.net_score || 0;
    const pointerPos = 50 + (netScore / maxScore * 50);
    document.getElementById("scorePointer").style.left = `${Math.min(Math.max(pointerPos, 2), 98)}%`;

    // Regime / Momentum / Confluence meta chips
    const metaEl = document.getElementById("verdictMeta");
    if (ov.market_regime || ov.momentum || ov.tf_confluence) {
        metaEl.style.display = "flex";

        // Makro rejim bilgisi (MarketRegime engine'den)
        const mr = ov.macro_regime || {};
        const macroMeta = {
            "RISK_ON": { icon: "🟢", label: "Risk-On", color: "var(--green)" },
            "ALT_SEASON": { icon: "🚀", label: "Alt Season", color: "var(--purple, #a855f7)" },
            "RISK_OFF": { icon: "🔴", label: "Risk-Off", color: "var(--red)" },
            "CAPITULATION": { icon: "☠️", label: "Kapitülasyon", color: "#f97316" },
            "NEUTRAL": { icon: "⚪", label: "Nötr", color: "var(--text-muted)" },
            "UNKNOWN": { icon: "❓", label: "Bilinmiyor", color: "var(--text-muted)" },
        };

        // ADX yapısı + makro rejim birlikte
        const adxRegime = ov.market_regime || "Normal";
        const adxIcons = {"Trend piyasası": "📈", "Yatay piyasa": "↔️", "Normal": "🔄"};
        const macro = macroMeta[mr.regime] || macroMeta.UNKNOWN;

        // RS skoru varsa ekle
        let rsText = "";
        if (mr.rs_score != null) {
            const rsSign = mr.rs_score >= 0 ? "+" : "";
            rsText = ` | RS: ${rsSign}${mr.rs_score.toFixed(1)} (#${mr.rs_rank || "?"})`;
        }

        document.getElementById("metaRegime").innerHTML = `${macro.icon} ${macro.label} ${adxIcons[adxRegime] || ""} ${adxRegime}${rsText}`;
        document.getElementById("metaRegime").style.borderColor = macro.color;

        const momLabels = {
            "BULL_ACCELERATING": "🚀 Boğa Hızlanıyor", "BEAR_ACCELERATING": "🚀 Ayı Hızlanıyor",
            "BULL_FADING": "📉 Boğa Zayıflıyor", "BEAR_FADING": "📉 Ayı Zayıflıyor",
            "BULL_REVERSAL_RISK": "🔄 Dönüş Riski", "BEAR_REVERSAL_RISK": "🔄 Dip Oluşumu",
            "NEUTRAL": "➖ Nötr İvme"
        };
        const momColors = {
            "BULL_ACCELERATING": "var(--green)", "BEAR_ACCELERATING": "var(--red)",
            "BULL_FADING": "var(--yellow, #f0ad4e)", "BEAR_FADING": "var(--yellow, #f0ad4e)",
            "BULL_REVERSAL_RISK": "var(--red)", "BEAR_REVERSAL_RISK": "var(--green)",
            "NEUTRAL": "var(--text-muted)"
        };
        const mom = ov.momentum || "NEUTRAL";
        document.getElementById("metaMomentum").innerHTML = momLabels[mom] || "➖ Nötr";
        document.getElementById("metaMomentum").style.borderColor = momColors[mom] || "var(--text-muted)";

        const confLabels = {"ALL_BULL": "✅ Tam Boğa Uyumu", "ALL_BEAR": "✅ Tam Ayı Uyumu", "MIXED": "⚡ Karışık TF"};
        const confColors = {"ALL_BULL": "var(--green)", "ALL_BEAR": "var(--red)", "MIXED": "var(--yellow, #f0ad4e)"};
        const conf = ov.tf_confluence || "MIXED";
        // TF çatışma varsa özel gösterim
        if (ov.tf_conflict) {
            document.getElementById("metaConfluence").innerHTML = "🚨 TF Çatışma!";
            document.getElementById("metaConfluence").style.borderColor = "var(--red)";
        } else {
            document.getElementById("metaConfluence").innerHTML = confLabels[conf] || "⚡ Karışık";
            document.getElementById("metaConfluence").style.borderColor = confColors[conf] || "var(--text-muted)";
        }
    } else {
        metaEl.style.display = "none";
    }

    // Warnings
    const warningsEl = document.getElementById("verdictWarnings");
    if (ov.warnings && ov.warnings.length > 0) {
        warningsEl.innerHTML = ov.warnings.map(w => `<div class="warning-item">${w}</div>`).join("");
    } else {
        warningsEl.innerHTML = "";
    }

    // Order Book
    const ob = data.orderbook || {};
    if (ob.imbalance != null) {
        document.getElementById("orderbookSection").style.display = "block";
        const obBadge = document.getElementById("obBadge");
        obBadge.textContent = ob.label || "--";
        obBadge.className = `ind-badge ${getSignalClass(ob.signal)}`;
        document.getElementById("obBidFill").style.width = `${ob.imbalance}%`;
        document.getElementById("obAskFill").style.width = `${100 - ob.imbalance}%`;
        document.getElementById("obBidText").textContent = `Alım: %${ob.imbalance}`;
        document.getElementById("obAskText").textContent = `Satım: %${(100 - ob.imbalance).toFixed(1)}`;
        document.getElementById("obSpread").textContent = `Spread: %${ob.spread_pct || 0}`;
        document.getElementById("obDesc").textContent = ob.desc || "";
    } else {
        document.getElementById("orderbookSection").style.display = "none";
    }

    // Show TF section
    document.getElementById("tfSection").style.display = "block";

    // Set active tab
    document.querySelectorAll(".tf-tab").forEach(t => t.classList.toggle("active", t.dataset.tf === "15m"));

    renderModalTf("15m");

    // Render Market Data
    renderMarketData(data.market_data);
}

// =================== PİYASA VERİLERİ ===================

function renderMarketData(md) {
    const section = document.getElementById("marketDataSection");
    if (!md) {
        section.style.display = "none";
        return;
    }

    const hasFunding = md.funding && md.funding.current != null;
    const hasOI = md.open_interest && md.open_interest.usdt;
    const hasLSR = md.long_short_ratio && Object.keys(md.long_short_ratio).length > 0;

    if (!hasFunding && !hasOI && !hasLSR) {
        section.style.display = "none";
        return;
    }
    section.style.display = "block";

    // Fonlama oranı
    if (hasFunding) {
        const f = md.funding;
        document.getElementById("mdFunding").style.display = "block";
        const fBadge = document.getElementById("fundingBadge");
        fBadge.textContent = f.label || "--";
        fBadge.className = `ind-badge ${getSignalClass(f.signal)}`;
        document.getElementById("fundingCurrent").textContent = `${f.current}%`;
        document.getElementById("fundingNext").textContent = `${f.next}%`;
        document.getElementById("fundingTime").textContent = f.next_time || "--";
        document.getElementById("fundingDesc").textContent = f.desc || "";
    } else {
        document.getElementById("mdFunding").style.display = "none";
    }

    // Açık Faiz
    if (hasOI) {
        const oi = md.open_interest;
        document.getElementById("mdOI").style.display = "block";
        const oiBadge = document.getElementById("oiBadge");
        oiBadge.textContent = oi.display || "--";
        oiBadge.className = `ind-badge ${getSignalClass(oi.signal)}`;
        document.getElementById("oiValue").textContent = oi.display || "--";
        document.getElementById("oiDesc").textContent = oi.desc || "";
    } else {
        document.getElementById("mdOI").style.display = "none";
    }

    // Long/Short Ratio
    if (hasLSR) {
        document.getElementById("mdLSR").style.display = "block";
        const periodsEl = document.getElementById("lsrPeriods");
        const periodLabels = { "5m": "5 Dakika", "1H": "1 Saat", "1D": "1 Gün" };
        let html = "";
        for (const [key, val] of Object.entries(md.long_short_ratio)) {
            const label = periodLabels[key] || key;
            const longW = val.long_pct || 50;
            const shortW = val.short_pct || 50;
            html += `
            <div class="lsr-period">
                <div class="lsr-period-head">
                    <span class="lsr-period-label">${label}</span>
                    <span class="ind-badge ${getSignalClass(val.signal)}">${val.label}</span>
                </div>
                <div class="lsr-bar">
                    <div class="lsr-long" style="width:${longW}%">
                        <span>L ${longW}%</span>
                    </div>
                    <div class="lsr-short" style="width:${shortW}%">
                        <span>S ${shortW}%</span>
                    </div>
                </div>
                <p class="ind-desc">${val.desc || ""}</p>
            </div>`;
        }
        periodsEl.innerHTML = html;
    } else {
        document.getElementById("mdLSR").style.display = "none";
    }
}

function switchModalTf(tf) {
    currentModalTf = tf;
    document.querySelectorAll(".tf-tab").forEach(t => t.classList.toggle("active", t.dataset.tf === tf));
    renderModalTf(tf);
}

function renderModalTf(tf) {
    if (!coinDetailData || !coinDetailData.timeframes) return;

    const tfData = coinDetailData.timeframes[tf];
    if (!tfData) return;

    // TF Verdict chip with score
    const verdictRow = document.getElementById("tfVerdictRow");
    const chipClass = {
        "STRONG_BULLISH": "bull", "BULLISH": "bull", "BEARISH": "bear", "STRONG_BEARISH": "bear",
        "NEUTRAL": "neutral", "LEANING_BULLISH": "weak-bull", "LEANING_BEARISH": "weak-bear", "UNKNOWN": "neutral"
    }[tfData.verdict] || "neutral";

    const verdictEmoji = {
        "STRONG_BULLISH": "🟢🟢", "BULLISH": "🟢", "BEARISH": "🔴", "STRONG_BEARISH": "🔴🔴",
        "NEUTRAL": "⚪", "LEANING_BULLISH": "🟡", "LEANING_BEARISH": "🟠"
    }[tfData.verdict] || "⚪";

    const netScore = tfData.net_score || 0;
    const confidence = tfData.confidence || 0;
    const direction = tfData.direction || "NONE";
    const dirLabel = direction === "LONG" ? "📈 LONG" : direction === "SHORT" ? "📉 SHORT" : "⏳ YÖN YOK";

    // TF rolü açıklaması
    const tfRole = {
        "15m": "Giriş Zamanlaması",
        "1H": "Ara Onay",
        "4H": "Ana Trend"
    }[tf] || "";

    verdictRow.innerHTML = `
        <span class="tf-verdict-chip ${chipClass}">${verdictEmoji} ${tfData.verdict_label || tfData.verdict}</span>
        <span class="tf-direction-chip ${direction.toLowerCase()}">${dirLabel}</span>
        <span class="tf-verdict-text" style="font-weight:600">${tfRole}</span>
        <span class="tf-verdict-text">Skor: ${netScore > 0 ? '+' : ''}${netScore} | Boğa: ${tfData.bull_score || 0} | Ayı: ${tfData.bear_score || 0}</span>
    `;

    // ═══ Pillar Score Summary (3 sütun + destek) ═══
    const pillars = tfData.pillar_scores || {};
    const summaryEl = document.getElementById("pillarScoreSummary");
    let summaryHtml = '<div class="pillar-bars">';
    const pillarConfig = [
        { key: "donchian", icon: "fas fa-arrows-alt-v", color: "#3498db" },
        { key: "vwap_dpo", icon: "fas fa-balance-scale", color: "#9b59b6" },
        { key: "money_flow", icon: "fas fa-money-bill-wave", color: "#2ecc71" },
        { key: "support_adj", icon: "fas fa-info-circle", color: "#95a5a6" },
    ];
    for (const cfg of pillarConfig) {
        const p = pillars[cfg.key];
        if (!p) continue;
        const pct = Math.min(Math.abs(p.score) / p.max * 100, 100);
        const isNeg = p.score < 0;
        const scoreText = cfg.key === "support_adj" ? (p.score >= 0 ? `+${p.score}` : `${p.score}`) : `${p.score}/${p.max}`;
        const barColor = isNeg ? "var(--red)" : cfg.color;
        const flowDir = p.direction ? (p.direction === "BULL" ? " 🐂" : p.direction === "BEAR" ? " 🐻" : "") : "";
        summaryHtml += `
            <div class="pillar-bar-item">
                <div class="pillar-label"><i class="${cfg.icon}"></i> ${p.label}${flowDir}</div>
                <div class="pillar-track">
                    <div class="pillar-fill" style="width:${pct}%;background:${barColor}"></div>
                </div>
                <div class="pillar-score">${scoreText}</div>
            </div>`;
    }
    summaryHtml += '</div>';
    summaryEl.innerHTML = summaryHtml;

    // ═══ ANA STRATEJİ: Donchian Channel ═══
    const dc = tfData.donchian || {};
    const dcBadge = document.getElementById("donchianBadge");
    dcBadge.textContent = dc.label || "--";
    dcBadge.className = `ind-badge ${getSignalClass(dc.signal)}`;
    document.getElementById("dcUpper").textContent = dc.upper != null ? formatPrice(dc.upper) : "--";
    document.getElementById("dcMiddle").textContent = dc.middle != null ? formatPrice(dc.middle) : "--";
    document.getElementById("dcLower").textContent = dc.lower != null ? formatPrice(dc.lower) : "--";
    document.getElementById("dcWidth").textContent = dc.width_pct != null ? `%${dc.width_pct.toFixed(2)}` : "--";
    document.getElementById("donchianDesc").textContent = dc.desc || "";
    // Position dot
    const dcDot = document.getElementById("dcPositionDot");
    if (dc.upper != null && dc.lower != null && dc.upper !== dc.lower) {
        const currentPrice = coinDetailData?.price?.last || 0;
        const dcPct = ((currentPrice - dc.lower) / (dc.upper - dc.lower)) * 100;
        dcDot.style.left = `${Math.min(Math.max(dcPct, 2), 98)}%`;
        dcDot.style.display = "block";
        dcDot.style.backgroundColor = dcPct > 80 ? "var(--green)" : dcPct < 20 ? "var(--red)" : "var(--blue)";
    } else {
        dcDot.style.display = "none";
    }

    // ═══ ANA STRATEJİ: VWAP + DPO ═══
    const vd = tfData.vwap_dpo || {};
    const vdBadge = document.getElementById("vwapDpoBadge");
    vdBadge.textContent = vd.label || "--";
    vdBadge.className = `ind-badge ${getSignalClass(vd.signal)}`;
    document.getElementById("vwapValue").textContent = vd.vwap != null ? formatPrice(vd.vwap) : "--";
    document.getElementById("vwapDeviation").textContent = vd.vwap_dev != null ? `${vd.vwap_dev > 0 ? '+' : ''}${vd.vwap_dev.toFixed(2)}σ` : "--";
    const vwapDevEl = document.getElementById("vwapDeviation");
    if (vd.vwap_dev != null) {
        vwapDevEl.style.color = Math.abs(vd.vwap_dev) > 2 ? "var(--red)" : Math.abs(vd.vwap_dev) < 0.5 ? "var(--green)" : "var(--text-primary)";
    }
    document.getElementById("dpoValue").textContent = vd.dpo != null ? vd.dpo.toFixed(4) : "--";
    document.getElementById("vwapDpoDesc").textContent = vd.desc || "";

    // ═══ ANA STRATEJİ: CMF ═══
    const cmf = tfData.cmf || {};
    const cmfBadge = document.getElementById("cmfBadge");
    cmfBadge.textContent = cmf.label || "--";
    cmfBadge.className = `ind-badge ${getSignalClass(cmf.signal)}`;
    const cmfVal = cmf.value;
    document.getElementById("cmfValue").textContent = cmfVal != null ? cmfVal.toFixed(4) : "--";
    document.getElementById("cmfValue").style.color = cmfVal > 0.05 ? "var(--green)" : cmfVal < -0.05 ? "var(--red)" : "var(--text-primary)";
    // CMF bar
    const cmfFill = document.getElementById("cmfBarFill");
    if (cmfVal != null) {
        const cmfPct = (cmfVal + 0.5) / 1.0 * 100; // -0.5 to 0.5 range
        cmfFill.style.width = `${Math.min(Math.max(cmfPct, 2), 98)}%`;
        cmfFill.style.backgroundColor = cmfVal > 0.05 ? "var(--green)" : cmfVal < -0.05 ? "var(--red)" : "var(--text-muted)";
    }
    document.getElementById("cmfDesc").textContent = cmf.desc || "";

    // ═══ ANA STRATEJİ: MFI ═══
    const mfi = tfData.mfi || {};
    const mfiBadge = document.getElementById("mfiBadge");
    mfiBadge.textContent = mfi.label || "--";
    mfiBadge.className = `ind-badge ${getSignalClass(mfi.signal)}`;
    const mfiVal = mfi.value;
    document.getElementById("mfiValue").textContent = mfiVal != null ? mfiVal.toFixed(1) : "--";
    document.getElementById("mfiValue").style.color = mfiVal > 80 ? "var(--red)" : mfiVal < 20 ? "var(--green)" : "var(--text-primary)";
    const mfiNeedle = document.getElementById("mfiNeedle");
    if (mfiVal != null) {
        mfiNeedle.style.left = `${Math.min(Math.max(mfiVal, 0), 100)}%`;
        mfiNeedle.style.display = "block";
    } else {
        mfiNeedle.style.display = "none";
    }
    document.getElementById("mfiDesc").textContent = mfi.desc || "";

    // RSI
    const rsi = tfData.rsi || {};
    const rsiBadge = document.getElementById("rsiBadge");
    rsiBadge.textContent = rsi.label || "--";
    rsiBadge.className = `ind-badge ${getSignalClass(rsi.signal)}`;
    document.getElementById("rsiValue").textContent = rsi.value != null ? rsi.value.toFixed(1) : "--";
    document.getElementById("rsiValue").style.color = getSignalColor(rsi.signal);
    document.getElementById("rsiDesc").textContent = rsi.desc || "";
    const needle = document.getElementById("rsiNeedle");
    if (rsi.value != null) {
        needle.style.left = `${Math.min(Math.max(rsi.value, 0), 100)}%`;
        needle.style.display = "block";
    } else {
        needle.style.display = "none";
    }

    // Stochastic RSI
    const stoch = tfData.stoch_rsi || {};
    const stochBadge = document.getElementById("stochRsiBadge");
    stochBadge.textContent = stoch.label || "--";
    stochBadge.className = `ind-badge ${getSignalClass(stoch.signal)}`;
    document.getElementById("stochK").textContent = stoch.k != null ? stoch.k.toFixed(1) : "--";
    document.getElementById("stochK").style.color = stoch.k > 80 ? "var(--red)" : stoch.k < 20 ? "var(--green)" : "var(--text-primary)";
    document.getElementById("stochD").textContent = stoch.d != null ? stoch.d.toFixed(1) : "--";
    document.getElementById("stochRsiDesc").textContent = stoch.desc || "";
    const stochNeedle = document.getElementById("stochNeedle");
    if (stoch.k != null) {
        stochNeedle.style.left = `${Math.min(Math.max(stoch.k, 0), 100)}%`;
        stochNeedle.style.display = "block";
    } else {
        stochNeedle.style.display = "none";
    }

    // MACD
    const macd = tfData.macd || {};
    const macdBadge = document.getElementById("macdBadge");
    macdBadge.textContent = macd.label || "--";
    macdBadge.className = `ind-badge ${getSignalClass(macd.signal_type)}`;
    document.getElementById("macdLine").textContent = macd.macd != null ? macd.macd.toFixed(6) : "--";
    document.getElementById("macdLine").style.color = macd.macd > 0 ? "var(--green)" : macd.macd < 0 ? "var(--red)" : "var(--text-primary)";
    document.getElementById("macdSignal").textContent = macd.signal != null ? macd.signal.toFixed(6) : "--";
    document.getElementById("macdHist").textContent = macd.histogram != null ? macd.histogram.toFixed(6) : "--";
    document.getElementById("macdHist").style.color = macd.histogram > 0 ? "var(--green)" : macd.histogram < 0 ? "var(--red)" : "var(--text-primary)";
    document.getElementById("macdDesc").textContent = macd.desc || "";

    // Bollinger Bands
    const bb = tfData.bollinger || {};
    const bbBadge = document.getElementById("bbBadge");
    bbBadge.textContent = bb.label || "--";
    bbBadge.className = `ind-badge ${getSignalClass(bb.signal)}`;
    document.getElementById("bbUpper").textContent = bb.upper != null ? formatPrice(bb.upper) : "--";
    document.getElementById("bbMiddle").textContent = bb.middle != null ? formatPrice(bb.middle) : "--";
    document.getElementById("bbLower").textContent = bb.lower != null ? formatPrice(bb.lower) : "--";
    document.getElementById("bbDesc").textContent = bb.desc || "";
    document.getElementById("bbSqueeze").textContent = bb.squeeze_desc || "";
    // BB position meter
    const bbPos = document.getElementById("bbPosition");
    if (bb.pct_b != null) {
        bbPos.style.left = `${Math.min(Math.max(bb.pct_b, 0), 100)}%`;
        bbPos.style.display = "block";
        bbPos.style.backgroundColor = bb.pct_b > 80 ? "var(--red)" : bb.pct_b < 20 ? "var(--green)" : "var(--blue)";
    } else {
        bbPos.style.display = "none";
    }

    // ADX
    const adx = tfData.adx || {};
    const adxBadge = document.getElementById("adxBadge");
    adxBadge.textContent = adx.label || "--";
    adxBadge.className = `ind-badge ${getSignalClass(adx.signal)}`;
    document.getElementById("adxValue").textContent = adx.adx != null ? adx.adx.toFixed(1) : "--";
    document.getElementById("adxPlusDi").textContent = adx.plus_di != null ? adx.plus_di.toFixed(1) : "--";
    document.getElementById("adxMinusDi").textContent = adx.minus_di != null ? adx.minus_di.toFixed(1) : "--";
    document.getElementById("adxDesc").textContent = adx.desc || "";
    // ADX strength bar
    const adxFill = document.getElementById("adxFill");
    if (adx.adx != null) {
        adxFill.style.width = `${Math.min(adx.adx, 75) / 75 * 100}%`;
        adxFill.style.backgroundColor = adx.adx >= 25 ? (adx.di_direction === "BULLISH" ? "var(--green)" : "var(--red)") : "var(--text-muted)";
    }

    // ATR
    const atr = tfData.atr || {};
    const atrBadge = document.getElementById("atrBadge");
    atrBadge.textContent = atr.label || "--";
    atrBadge.className = `ind-badge ${atr.signal === "HIGH" ? "bear" : atr.signal === "LOW" ? "weak-bull" : "neutral"}`;
    document.getElementById("atrValue").textContent = atr.atr != null ? formatPrice(atr.atr) : "--";
    document.getElementById("atrPct").textContent = atr.atr_pct != null ? `%${atr.atr_pct}` : "--";
    document.getElementById("atrDesc").textContent = atr.desc || "";

    // Volume
    const vol = tfData.volume || {};
    const volBadge = document.getElementById("volBadge");
    volBadge.textContent = vol.label || "--";
    volBadge.className = `ind-badge ${vol.signal === "HIGH" ? "bull" : vol.signal === "LOW" ? "weak-bear" : "neutral"}`;
    document.getElementById("volRatio").textContent = vol.ratio != null ? `${vol.ratio}x` : "--";
    document.getElementById("volRatio").style.color = vol.ratio >= 1.5 ? "var(--green)" : vol.ratio < 0.8 ? "var(--red)" : "var(--text-primary)";
    document.getElementById("volTrend").textContent = vol.trend || "--";
    document.getElementById("volTrend").style.color = vol.trend === "ARTIYOR" ? "var(--green)" : vol.trend === "AZALIYOR" ? "var(--red)" : "var(--text-primary)";
    document.getElementById("volDesc").textContent = vol.desc || "";
    document.getElementById("volHarmony").textContent = vol.price_vol_harmony || "";

    // OBV
    const obv = tfData.obv || {};
    const obvBadge = document.getElementById("obvBadge");
    obvBadge.textContent = obv.label || "--";
    obvBadge.className = `ind-badge ${getSignalClass(obv.signal)}`;
    document.getElementById("obvDesc").textContent = obv.desc || "";

    // FVG
    const fvg = tfData.fvg || {};
    const fvgBadge = document.getElementById("fvgBadge");
    fvgBadge.textContent = fvg.label || "--";
    fvgBadge.className = `ind-badge ${getSignalClass(fvg.signal)}`;
    document.getElementById("fvgBull").textContent = fvg.unfilled_bullish || 0;
    document.getElementById("fvgBear").textContent = fvg.unfilled_bearish || 0;
    document.getElementById("fvgDesc").textContent = fvg.desc || "";

    // Support/Resistance
    const sr = tfData.support_resistance || {};
    const srBadge = document.getElementById("srBadge");
    srBadge.textContent = sr.label || "--";
    srBadge.className = `ind-badge ${getSignalClass(sr.signal)}`;
    document.getElementById("srResistance").textContent = sr.nearest_resistance != null ? formatPrice(sr.nearest_resistance) : "--";
    document.getElementById("srSupport").textContent = sr.nearest_support != null ? formatPrice(sr.nearest_support) : "--";
    const currentPrice = coinDetailData?.price?.last || 0;
    document.getElementById("srCurrent").textContent = currentPrice ? formatPrice(currentPrice) : "--";
    document.getElementById("srDesc").textContent = sr.desc || "";

    // Divergence
    const div = tfData.divergence || {};
    const divBadge = document.getElementById("divBadge");
    divBadge.textContent = div.label || "--";
    const divClass = div.type === "BULLISH" ? "bull" : div.type === "BEARISH" ? "bear" : "neutral";
    divBadge.className = `ind-badge ${divClass}`;
    document.getElementById("divDesc").textContent = div.desc || "";
}

function getSignalClass(signal) {
    switch (signal) {
        case "BULLISH": case "STRONG_BULLISH": return "bull";
        case "BEARISH": case "STRONG_BEARISH": return "bear";
        case "OVERBOUGHT": return "bear";
        case "OVERSOLD": return "bull";
        case "IDEAL_ENTRY": case "FAIR_ENTRY": case "BOTTOM_FORMING": return "bull";
        case "TOP_FORMING": case "OVEREXTENDED_BULL": case "OVEREXTENDED_BEAR": case "STRETCHING_BULL": case "STRETCHING_BEAR": return "weak-bear";
        case "WEAKENING_BULL": return "weak-bull";
        case "WEAKENING_BEAR": return "weak-bear";
        default: return "neutral";
    }
}

function getSignalColor(signal) {
    switch (signal) {
        case "BULLISH": case "STRONG_BULLISH": case "IDEAL_ENTRY": case "BOTTOM_FORMING": case "OVERSOLD": return "var(--green)";
        case "BEARISH": case "STRONG_BEARISH": case "OVERBOUGHT": case "TOP_FORMING": return "var(--red)";
        case "OVEREXTENDED_BULL": case "OVEREXTENDED_BEAR": case "STRETCHING_BULL": case "STRETCHING_BEAR": return "var(--yellow, #f0ad4e)";
        default: return "var(--text-primary)";
    }
}

// =================== AUTO REFRESH ===================

let autoRefreshActive = false;
let autoRefreshInterval = null;
let autoRefreshCountdown = 30;
let autoRefreshCountdownInterval = null;

function toggleAutoRefresh() {
    if (autoRefreshActive) {
        stopAutoRefresh();
    } else {
        startAutoRefresh();
    }
}

function startAutoRefresh() {
    if (!coinDetailData?._symbol) return;
    autoRefreshActive = true;
    autoRefreshCountdown = 30;

    const toggle = document.getElementById("autoRefreshToggle");
    toggle.classList.add("active");
    document.getElementById("arSwitchDot").classList.add("on");
    updateCountdownDisplay();

    // Countdown
    autoRefreshCountdownInterval = setInterval(() => {
        autoRefreshCountdown--;
        updateCountdownDisplay();
        if (autoRefreshCountdown <= 0) {
            autoRefreshCountdown = 30;
            refreshCoinDetail();
        }
    }, 1000);
}

function stopAutoRefresh() {
    autoRefreshActive = false;
    if (autoRefreshCountdownInterval) clearInterval(autoRefreshCountdownInterval);
    autoRefreshCountdownInterval = null;

    const toggle = document.getElementById("autoRefreshToggle");
    toggle.classList.remove("active");
    document.getElementById("arSwitchDot").classList.remove("on");
    document.getElementById("arCountdown").textContent = "";
}

function updateCountdownDisplay() {
    document.getElementById("arCountdown").textContent = `${autoRefreshCountdown}s`;
}

async function refreshCoinDetail() {
    if (!coinDetailData?._symbol || !autoRefreshActive) return;

    try {
        const data = await apiFetch(`/api/coin-detail/${coinDetailData._symbol}`);
        if (!data || data.error) return;

        coinDetailData = data;
        coinDetailData._symbol = coinDetailData._symbol || data.symbol;

        // Update price
        const price = data.price || {};
        const change = price.change24h || 0;
        const changeStr = change >= 0 ? `+${change.toFixed(2)}%` : `${change.toFixed(2)}%`;
        const changeColor = change >= 0 ? "var(--green)" : "var(--red)";
        document.getElementById("modalCoinPrice").innerHTML =
            `${formatPrice(price.last)} <span style="color:${changeColor};font-size:12px;font-weight:600;margin-left:8px">${changeStr}</span>`;

        // Update verdict
        const ov = data.overall || {};
        const verdictType = ov.verdict || "NEUTRAL";
        document.getElementById("modalOverallVerdict").className = `detail-verdict-card verdict-${verdictType}`;
        document.getElementById("verdictLabel").textContent = `🤖 ${ov.label || "Bilinmiyor"}`;
        document.getElementById("verdictDesc").innerHTML = (ov.description || "").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\n/g, "<br>");

        // Score bar
        const bullTotal = ov.bull_total || 0;
        const bearTotal = ov.bear_total || 0;
        const maxScore = 100;
        document.getElementById("scoreFillBull").style.width = `${Math.min(bullTotal / maxScore * 50, 50)}%`;
        document.getElementById("scoreFillBear").style.width = `${Math.min(bearTotal / maxScore * 50, 50)}%`;
        const netScore = ov.net_score || 0;
        const pointerPos = 50 + (netScore / maxScore * 50);
        document.getElementById("scorePointer").style.left = `${Math.min(Math.max(pointerPos, 2), 98)}%`;

        // Meta chips (Regime / Momentum / Confluence)
        const metaEl = document.getElementById("verdictMeta");
        if (ov.market_regime || ov.momentum || ov.tf_confluence) {
            metaEl.style.display = "flex";

            // Makro rejim bilgisi
            const mr = ov.macro_regime || {};
            const macroMeta = {
                "RISK_ON": { icon: "🟢", label: "Risk-On", color: "var(--green)" },
                "ALT_SEASON": { icon: "🚀", label: "Alt Season", color: "var(--purple, #a855f7)" },
                "RISK_OFF": { icon: "🔴", label: "Risk-Off", color: "var(--red)" },
                "CAPITULATION": { icon: "☠️", label: "Kapitülasyon", color: "#f97316" },
                "NEUTRAL": { icon: "⚪", label: "Nötr", color: "var(--text-muted)" },
                "UNKNOWN": { icon: "❓", label: "Bilinmiyor", color: "var(--text-muted)" },
            };
            const adxRegime = ov.market_regime || "Normal";
            const adxIcons = {"Trend piyasası": "📈", "Yatay piyasa": "↔️", "Normal": "🔄"};
            const macro = macroMeta[mr.regime] || macroMeta.UNKNOWN;
            let rsText = "";
            if (mr.rs_score != null) {
                const rsSign = mr.rs_score >= 0 ? "+" : "";
                rsText = ` | RS: ${rsSign}${mr.rs_score.toFixed(1)} (#${mr.rs_rank || "?"})`;
            }
            document.getElementById("metaRegime").innerHTML = `${macro.icon} ${macro.label} ${adxIcons[adxRegime] || ""} ${adxRegime}${rsText}`;
            document.getElementById("metaRegime").style.borderColor = macro.color;

            const momLabels = {"BULL_ACCELERATING": "🚀 Boğa Hızlanıyor", "BEAR_ACCELERATING": "🚀 Ayı Hızlanıyor", "BULL_FADING": "📉 Boğa Zayıflıyor", "BEAR_FADING": "📉 Ayı Zayıflıyor", "BULL_REVERSAL_RISK": "🔄 Dönüş Riski", "BEAR_REVERSAL_RISK": "🔄 Dip Oluşumu", "NEUTRAL": "➖ Nötr İvme"};
            const momColors = {"BULL_ACCELERATING": "var(--green)", "BEAR_ACCELERATING": "var(--red)", "BULL_FADING": "var(--yellow, #f0ad4e)", "BEAR_FADING": "var(--yellow, #f0ad4e)", "BULL_REVERSAL_RISK": "var(--red)", "BEAR_REVERSAL_RISK": "var(--green)", "NEUTRAL": "var(--text-muted)"};
            const mom = ov.momentum || "NEUTRAL";
            document.getElementById("metaMomentum").innerHTML = momLabels[mom] || "➖ Nötr";
            document.getElementById("metaMomentum").style.borderColor = momColors[mom] || "var(--text-muted)";

            const confLabels = {"ALL_BULL": "✅ Tam Boğa Uyumu", "ALL_BEAR": "✅ Tam Ayı Uyumu", "MIXED": "⚡ Karışık TF"};
            const confColors = {"ALL_BULL": "var(--green)", "ALL_BEAR": "var(--red)", "MIXED": "var(--yellow, #f0ad4e)"};
            const conf = ov.tf_confluence || "MIXED";
            // TF çatışma varsa özel gösterim (openCoinDetail ile tutarlı)
            if (ov.tf_conflict) {
                document.getElementById("metaConfluence").innerHTML = "🚨 TF Çatışma!";
                document.getElementById("metaConfluence").style.borderColor = "var(--red)";
            } else {
                document.getElementById("metaConfluence").innerHTML = confLabels[conf] || "⚡ Karışık";
                document.getElementById("metaConfluence").style.borderColor = confColors[conf] || "var(--text-muted)";
            }
        } else {
            metaEl.style.display = "none";
        }

        // Warnings
        if (ov.warnings && ov.warnings.length > 0) {
            document.getElementById("verdictWarnings").innerHTML = ov.warnings.map(w => `<div class="warning-item">${w}</div>`).join("");
        } else {
            document.getElementById("verdictWarnings").innerHTML = "";
        }

        // Market Data
        renderMarketData(data.market_data);

        // Re-render current TF
        renderModalTf(currentModalTf);
    } catch (e) {
        console.error("Auto-refresh error:", e);
    }
}

// =================== FOREX & ALTIN ICT (OTOMATİK TARAMA) ===================

let forexCurrentTf = "1h";
let forexScanData = null;
let forexDetailData = null;
let forexAutoTimer = null;
let forexCountdown = 0;
let forexCountdownTimer = null;
let forexIsScanning = false;
const FOREX_SCAN_INTERVAL = 60; // saniye

function initForexAutoScan() {
    // Sekmeye ilk girişte otomatik tara
    if (!forexIsScanning) {
        scanForex();
    }
    startForexAutoTimer();
}

function startForexAutoTimer() {
    stopForexAutoTimer();
    forexCountdown = FOREX_SCAN_INTERVAL;
    updateForexCountdown();
    forexCountdownTimer = setInterval(() => {
        forexCountdown--;
        updateForexCountdown();
        if (forexCountdown <= 0) {
            scanForex();
            forexCountdown = FOREX_SCAN_INTERVAL;
        }
    }, 1000);
}

function stopForexAutoTimer() {
    if (forexCountdownTimer) clearInterval(forexCountdownTimer);
    if (forexAutoTimer) clearInterval(forexAutoTimer);
    forexCountdownTimer = null;
    forexAutoTimer = null;
}

function updateForexCountdown() {
    const el = document.getElementById("fxAutoCountdown");
    if (el) el.textContent = `${forexCountdown}s`;
    const badge = document.getElementById("fxAutoBadge");
    if (badge) badge.classList.toggle("scanning", forexIsScanning);
}

async function loadForexKillZones() {
    const data = await apiFetch("/api/forex/kill-zones");
    if (!data) return;

    const bar = document.getElementById("fxSessionBar");
    const icon = document.getElementById("fxSessionIcon");
    if (bar) {
        bar.classList.toggle("active", data.is_kill_zone);
        bar.classList.toggle("silver-bullet", false);
    }
    document.getElementById("kzLabel").textContent = data.is_kill_zone
        ? `${data.active_zone} Kill Zone Aktif`
        : "Kill Zone Dışında";
    document.getElementById("kzDesc").textContent = data.desc;

    if (icon) {
        icon.innerHTML = data.is_kill_zone
            ? '<i class="fas fa-crosshairs"></i>'
            : '<i class="fas fa-clock"></i>';
    }

    const zonesEl = document.getElementById("kzZones");
    if (zonesEl) {
        zonesEl.innerHTML = data.zones.map(z => {
            const nameMap = {"Asian Kill Zone": "Asya", "London Kill Zone": "Londra", "New York Kill Zone": "New York"};
            const label = nameMap[z.name] || z.name.split(' ')[0];
            const estInfo = z.est_hours ? ` (${z.est_hours})` : "";
            return `<span class="kz-zone-pill ${z.active ? 'active' : ''}" title="${z.desc || ''}">${label} ${z.hours}${estInfo}</span>`;
        }).join("");
    }

    // Sonraki KZ bilgisi
    if (data.next_kz && !data.is_kill_zone) {
        document.getElementById("kzDesc").textContent = data.desc;
    }
}

function switchForexTf(tf) {
    forexCurrentTf = tf;
    document.querySelectorAll(".fx-tf-btn").forEach(b => b.classList.toggle("active", b.dataset.fxtf === tf));
    scanForex();
    forexCountdown = FOREX_SCAN_INTERVAL;
}

async function scanForex() {
    if (forexIsScanning) return;
    forexIsScanning = true;

    const grid = document.getElementById("fxSignalsGrid");
    const loading = document.getElementById("fxLoading");

    loading.style.display = "flex";
    if (grid && !forexScanData) grid.style.display = "none";

    updateForexCountdown();
    loadForexKillZones();

    const data = await apiFetch(`/api/forex/scan?tf=${forexCurrentTf}`);
    forexIsScanning = false;
    loading.style.display = "none";
    updateForexCountdown();

    // Son güncelleme zamanı
    const updateEl = document.getElementById("fxLastUpdate");
    const now = new Date();
    const timeStr = now.toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

    if (!data || !data.results || data.results.length === 0) {
        if (updateEl) updateEl.innerHTML = `<i class="fas fa-exclamation-triangle" style="color:var(--yellow)"></i><span>Veri alınamadı - ${timeStr}</span>`;
        if (!forexScanData) {
            grid.style.display = "grid";
            grid.innerHTML = `<div class="fx-no-data"><i class="fas fa-wifi-weak"></i><p>Forex verileri geçici olarak erişilemez. Otomatik yeniden denenecek...</p></div>`;
        }
        return;
    }

    if (updateEl) updateEl.innerHTML = `<i class="fas fa-check-circle" style="color:var(--green)"></i><span>Son güncelleme: ${timeStr}</span>`;

    forexScanData = data.results;

    // Sinyal sıralaması: güçlü sinyaller önce
    const signalOrder = { "STRONG_LONG": 0, "STRONG_SHORT": 1, "LONG": 2, "SHORT": 3, "WAIT": 4 };
    data.results.sort((a, b) => (signalOrder[a.signal] || 9) - (signalOrder[b.signal] || 9));

    grid.style.display = "grid";
    grid.innerHTML = data.results.map(r => renderForexCard(r)).join("");

    // Nav badge güncelle
    const activeSigs = data.results.filter(r => r.signal !== "WAIT").length;
    const badge = document.getElementById("navBadgeForex");
    if (badge) {
        badge.textContent = activeSigs > 0 ? activeSigs : "";
        badge.style.display = activeSigs > 0 ? "inline-flex" : "none";
    }
}

function renderForexCard(r) {
    const totalScore = r.bull_score + r.bear_score || 1;
    const bullPct = (r.bull_score / totalScore * 100).toFixed(0);
    const bearPct = (r.bear_score / totalScore * 100).toFixed(0);
    const netAbs = Math.abs(r.net_score);
    const confMax = Math.max(r.confluence_bull || 0, r.confluence_bear || 0);

    // ICT tag'leri
    const tags = [];
    if (r.market_structure.trend !== "NEUTRAL") {
        const trendCls = r.market_structure.trend === "BULLISH" ? "bull" : "bear";
        tags.push(`<span class="fx-tag ${trendCls}"><i class="fas fa-${r.market_structure.trend === 'BULLISH' ? 'arrow-trend-up' : 'arrow-trend-down'}"></i> ${r.market_structure.trend === "BULLISH" ? "Yükseliş" : "Düşüş"}</span>`);
    }
    if (r.market_structure.choch) {
        const chCls = r.market_structure.choch.type.includes("BULL") ? "bull" : "bear";
        tags.push(`<span class="fx-tag ${chCls}"><i class="fas fa-rotate"></i> CHoCH</span>`);
    }
    if (r.market_structure.bos.length > 0) {
        const bosCls = r.market_structure.bos[0].type.includes('BULL') ? 'bull' : 'bear';
        tags.push(`<span class="fx-tag ${bosCls}"><i class="fas fa-bolt"></i> BOS</span>`);
    }
    if (r.displacement && r.displacement.length > 0) {
        const lastD = r.displacement[r.displacement.length - 1];
        tags.push(`<span class="fx-tag ${lastD.type.includes('BULL') ? 'bull' : 'bear'}"><i class="fas fa-bolt-lightning"></i> DISP</span>`);
    }
    if (r.fvg && (r.fvg.bull > 0 || r.fvg.bear > 0)) {
        const fvgDir = r.fvg.bull > r.fvg.bear ? "bull" : "bear";
        const ceCount = r.fvg.ce_bull + r.fvg.ce_bear;
        tags.push(`<span class="fx-tag ${fvgDir}"><i class="fas fa-layer-group"></i> FVG${ceCount > 0 ? '+CE' : ''}</span>`);
    }
    if (r.order_blocks && r.order_blocks.length > 0) {
        const obDir = r.order_blocks[0].type.includes("BULL") ? "bull" : "bear";
        tags.push(`<span class="fx-tag ${obDir}"><i class="fas fa-cube"></i> OB</span>`);
    }
    if (r.liquidity_sweeps && r.liquidity_sweeps.length > 0) {
        const lsDir = r.liquidity_sweeps[r.liquidity_sweeps.length-1].type === "BUY_SIDE_SWEEP" ? "bull" : "bear";
        tags.push(`<span class="fx-tag ${lsDir}"><i class="fas fa-water"></i> Sweep</span>`);
    }
    if (r.ote) {
        tags.push(`<span class="fx-tag ${r.ote.direction === 'LONG' ? 'bull' : 'bear'}"><i class="fas fa-crosshairs"></i> OTE</span>`);
    }
    if (r.premium_discount.zone === "DISCOUNT") {
        tags.push(`<span class="fx-tag bull"><i class="fas fa-tag"></i> Discount</span>`);
    } else if (r.premium_discount.zone === "PREMIUM") {
        tags.push(`<span class="fx-tag bear"><i class="fas fa-tag"></i> Premium</span>`);
    }
    if (r.amd) {
        tags.push(`<span class="fx-tag ${r.amd.direction === 'LONG' ? 'bull' : 'bear'}"><i class="fas fa-recycle"></i> AMD</span>`);
    }
    if (r.judas) {
        tags.push(`<span class="fx-tag ${r.judas.type.includes('BULL') ? 'bull' : 'bear'}"><i class="fas fa-masks-theater"></i> Judas</span>`);
    }
    if (r.smart_money_trap) {
        tags.push(`<span class="fx-tag ${r.smart_money_trap.type === 'BEAR_TRAP' ? 'bull' : 'bear'}"><i class="fas fa-skull-crossbones"></i> SMT</span>`);
    }

    const fmtPrice = (v) => {
        if (v >= 100) return v.toFixed(2);
        if (v >= 1) return v.toFixed(4);
        return v.toFixed(5);
    };

    // Sinyal ikon ve renk
    const signalIcons = {
        STRONG_LONG: { icon: "fa-angles-up", cls: "strong-long" },
        LONG: { icon: "fa-arrow-up", cls: "long" },
        STRONG_SHORT: { icon: "fa-angles-down", cls: "strong-short" },
        SHORT: { icon: "fa-arrow-down", cls: "short" },
        WAIT: { icon: "fa-pause", cls: "wait" }
    };
    const si = signalIcons[r.signal] || signalIcons.WAIT;

    // SL/TP mini bilgisi
    let slTpMini = "";
    if (r.sl_tp) {
        const rrText = r.sl_tp.rr1 ? ` <span class="rr"><i class="fas fa-scale-balanced"></i> R:R 1:${r.sl_tp.rr1}</span>` : "";
        slTpMini = `<div class="fx-card-sltp">
            <span class="sl"><i class="fas fa-shield-halved"></i> SL: ${fmtPrice(r.sl_tp.sl)}</span>
            <span class="tp"><i class="fas fa-bullseye"></i> TP1: ${fmtPrice(r.sl_tp.tp1)}</span>
            ${rrText}
        </div>`;
    }

    // Daily Bias mini
    let biasIndicator = "";
    if (r.daily_bias && r.daily_bias.bias !== "NEUTRAL") {
        const biasCls = r.daily_bias.bias === "BULLISH" ? "bull" : "bear";
        biasIndicator = `<span class="fx-card-bias ${biasCls}"><i class="fas fa-compass"></i> HTF: ${r.daily_bias.bias === "BULLISH" ? "Yükseliş" : "Düşüş"}</span>`;
    }

    // Yorum özeti (kart altında kısa yorum)
    let commentarySummary = "";
    if (r.commentary && r.commentary.summary) {
        const sum = r.commentary.summary.length > 160 ? r.commentary.summary.substring(0, 157) + "..." : r.commentary.summary;
        commentarySummary = `<div class="fx-card-commentary"><i class="fas fa-brain"></i> ${sum}</div>`;
    }

    return `
    <div class="fx-card signal-${r.signal}" onclick="openForexDetail('${r.instrument}')">
        <div class="fx-card-header">
            <div class="fx-card-left">
                <div class="fx-card-icon-wrap">${r.icon}</div>
                <div class="fx-card-identity">
                    <span class="fx-card-name">${r.name}</span>
                    <span class="fx-card-desc">${r.desc}</span>
                </div>
            </div>
            <div class="fx-card-signal-badge ${si.cls}">
                <i class="fas ${si.icon}"></i>
                <span>${r.label}</span>
            </div>
        </div>

        <div class="fx-card-body">
            <div class="fx-card-price-row">
                <span class="fx-card-price">${fmtPrice(r.price)}</span>
                ${biasIndicator}
            </div>
            <div class="fx-card-tags">${tags.join("")}</div>
            ${slTpMini}
            ${commentarySummary}
        </div>

        <div class="fx-card-bottom">
            <div class="fx-card-meter">
                <div class="fx-card-meter-bear" style="width:${bearPct}%"></div>
                <div class="fx-card-meter-bull" style="width:${bullPct}%"></div>
            </div>
            <div class="fx-card-stats">
                <span class="stat-item"><i class="fas fa-signal"></i> Net: ${r.net_score > 0 ? '+' : ''}${r.net_score}</span>
                <span class="stat-item"><i class="fas fa-layer-group"></i> Uyum: ${confMax}/16</span>
                <span class="stat-item"><i class="fas fa-gauge-simple"></i> RSI: ${r.indicators.rsi.toFixed(1)}</span>
                <span class="stat-item"><i class="fas fa-chart-area"></i> ATR: ${r.indicators.atr_pct}%</span>
            </div>
        </div>
    </div>`;
}

async function openForexDetail(instrument) {
    const grid = document.getElementById("fxSignalsGrid");
    const panel = document.getElementById("fxDetailPanel");
    const tfBar = document.querySelector(".fx-tf-bar");
    const sessionBar = document.getElementById("fxSessionBar");

    grid.style.display = "none";
    if (tfBar) tfBar.style.display = "none";
    if (sessionBar) sessionBar.style.display = "none";
    panel.style.display = "block";

    // Fetch fresh data
    const data = await apiFetch(`/api/forex/signal/${instrument}?tf=${forexCurrentTf}`);
    if (!data || data.error) {
        panel.innerHTML = `<p style="color:var(--red)">Veri alınamadı: ${data?.error || "Bilinmeyen hata"}</p>`;
        return;
    }

    forexDetailData = data;

    const fmtPrice = (v) => {
        if (v >= 100) return v.toFixed(2);
        if (v >= 1) return v.toFixed(4);
        return v.toFixed(5);
    };

    // Header
    document.getElementById("fxDetIcon").textContent = data.icon;
    document.getElementById("fxDetName").textContent = data.name;
    document.getElementById("fxDetPrice").textContent = fmtPrice(data.price);

    const sigBadge = document.getElementById("fxDetSignalBadge");
    sigBadge.textContent = data.label;
    sigBadge.className = `fx-detail-signal fx-card-signal ${data.signal}`;

    // Verdict
    document.getElementById("fxDetVerdictLabel").textContent = data.label;
    document.getElementById("fxDetVerdictDesc").textContent = data.description;

    const totalScore = data.bull_score + data.bear_score || 1;
    document.getElementById("fxScoreBear").style.width = `${data.bear_score / totalScore * 100}%`;
    document.getElementById("fxScoreBull").style.width = `${data.bull_score / totalScore * 100}%`;
    document.getElementById("fxScoreBearVal").textContent = data.bear_score;
    document.getElementById("fxScoreBullVal").textContent = data.bull_score;

    // ICT Teknik Yorum
    const commentaryEl = document.getElementById("fxCommentary");
    const commentarySection = document.getElementById("fxCommentarySection");
    if (data.commentary && data.commentary.sections && data.commentary.sections.length > 0) {
        commentarySection.style.display = "block";
        let comHtml = "";
        for (const sec of data.commentary.sections) {
            const isConclusion = sec.title.includes("Sonuc") || sec.title.includes("Oneri");
            const iconMap = {
                "Genel Bakis": "fa-binoculars",
                "Piyasa Yapisi": "fa-sitemap",
                "Emir Bloklari (OB)": "fa-cubes",
                "Adil Deger Bosluklari (FVG)": "fa-layer-group",
                "Likidite Analizi": "fa-water",
                "Momentum / Displacement": "fa-bolt-lightning",
                "Premium/Indirim & OTE": "fa-percentage",
                "Gunluk Yon & Ozel Paternler": "fa-compass",
                "Seans & Zamanlama": "fa-clock",
                "Teknik Gostergeler": "fa-gauge-high",
                "Sonuc & Oneri": "fa-flag-checkered",
            };
            const icon = iconMap[sec.title] || "fa-circle-info";
            comHtml += `
                <div class="fx-com-block ${isConclusion ? 'conclusion' : ''}">
                    <div class="fx-com-title"><i class="fas ${icon}"></i> ${sec.title}</div>
                    <p class="fx-com-text">${sec.text}</p>
                </div>`;
        }
        commentaryEl.innerHTML = comHtml;
    } else {
        commentarySection.style.display = "none";
    }

    // SL / TP
    const slTpSection = document.getElementById("fxSlTpSection");
    const levelsGrid = document.getElementById("fxLevelsGrid");
    if (data.sl_tp) {
        slTpSection.style.display = "block";
        const rrInfo = data.sl_tp.rr1 ? `
            <div class="fx-level-item">
                <div class="fx-level-label rr">Risk:Ödül</div>
                <div class="fx-level-val">1:${data.sl_tp.rr1} / 1:${data.sl_tp.rr2}</div>
            </div>` : "";
        levelsGrid.innerHTML = `
            <div class="fx-level-item">
                <div class="fx-level-label entry">Giriş</div>
                <div class="fx-level-val">${fmtPrice(data.price)}</div>
            </div>
            <div class="fx-level-item">
                <div class="fx-level-label sl">Stop Loss</div>
                <div class="fx-level-val">${fmtPrice(data.sl_tp.sl)}</div>
            </div>
            <div class="fx-level-item">
                <div class="fx-level-label tp">Take Profit 1</div>
                <div class="fx-level-val">${fmtPrice(data.sl_tp.tp1)}</div>
            </div>
            <div class="fx-level-item">
                <div class="fx-level-label tp">Take Profit 2</div>
                <div class="fx-level-val">${fmtPrice(data.sl_tp.tp2)}</div>
            </div>
            ${rrInfo}
        `;
    } else {
        slTpSection.style.display = "none";
    }

    // Market Structure
    const msCard = document.getElementById("fxMsCard");
    const ms = data.market_structure;
    const trendIcon = ms.trend === "BULLISH" ? "fa-arrow-trend-up" : ms.trend === "BEARISH" ? "fa-arrow-trend-down" : "fa-arrows-left-right";
    const trendColor = ms.trend === "BULLISH" ? "var(--green)" : ms.trend === "BEARISH" ? "var(--red)" : "var(--yellow)";
    let msHtml = `
        <div class="fx-ms-row">
            <i class="fas ${trendIcon}" style="color:${trendColor}"></i>
            <span class="label">Piyasa Yapısı</span>
            <span class="value" style="color:${trendColor}">${ms.trend === "BULLISH" ? "Yükseliş Trendi (HH+HL)" : ms.trend === "BEARISH" ? "Düşüş Trendi (LH+LL)" : "Nötr / Kararsız"}</span>
        </div>`;

    for (const bos of ms.bos) {
        const bosColor = bos.type.includes("BULL") ? "var(--green)" : "var(--red)";
        msHtml += `<div class="fx-ms-row"><i class="fas fa-bolt" style="color:${bosColor}"></i><span class="label">Break of Structure (BOS)</span><span class="value" style="color:${bosColor}">${bos.type.replace("_", " ")} @ ${fmtPrice(bos.level)}</span></div>`;
    }
    if (ms.choch) {
        const chochColor = ms.choch.type.includes("BULL") ? "var(--green)" : "var(--red)";
        msHtml += `<div class="fx-ms-row"><i class="fas fa-rotate" style="color:${chochColor}"></i><span class="label">Change of Character (CHoCH)</span><span class="value" style="color:${chochColor}">${ms.choch.desc}</span></div>`;
    }

    if (ms.swing_highs && ms.swing_highs.length > 0) {
        const lastSH = ms.swing_highs[ms.swing_highs.length - 1];
        msHtml += `<div class="fx-ms-row"><i class="fas fa-arrow-up" style="color:var(--green)"></i><span class="label">Son Swing High</span><span class="value">${fmtPrice(lastSH.price)}</span></div>`;
    }
    if (ms.swing_lows && ms.swing_lows.length > 0) {
        const lastSL = ms.swing_lows[ms.swing_lows.length - 1];
        msHtml += `<div class="fx-ms-row"><i class="fas fa-arrow-down" style="color:var(--red)"></i><span class="label">Son Swing Low</span><span class="value">${fmtPrice(lastSL.price)}</span></div>`;
    }
    msCard.innerHTML = msHtml;

    // Order Blocks
    const obList = document.getElementById("fxObList");
    if (data.order_blocks && data.order_blocks.length > 0) {
        obList.innerHTML = data.order_blocks.map(ob => `
            <div class="fx-ob-item ${ob.type.includes('BULL') ? 'bull' : 'bear'}">
                <span class="ob-type">${ob.type.includes("BULL") ? "🟢 Bullish OB" : "🔴 Bearish OB"}</span>
                <span class="ob-range">${fmtPrice(ob.low)} — ${fmtPrice(ob.high)}</span>
                <span class="ob-str">Güç: ${ob.strength}x</span>
            </div>`).join("");
    } else {
        obList.innerHTML = `<div class="fx-no-item">Aktif Order Block bulunamadı</div>`;
    }

    // Breaker Blocks
    const breakerSection = document.getElementById("fxBreakerSection");
    const breakerList = document.getElementById("fxBreakerList");
    if (data.breaker_blocks && data.breaker_blocks.length > 0) {
        breakerSection.style.display = "block";
        breakerList.innerHTML = data.breaker_blocks.map(bb => `
            <div class="fx-ob-item ${bb.type.includes('BULL') ? 'bull' : 'bear'}">
                <span class="ob-type">${bb.type.includes("BULL") ? "🟢 Bullish Breaker" : "🔴 Bearish Breaker"}</span>
                <span class="ob-range">${fmtPrice(bb.low)} — ${fmtPrice(bb.high)}</span>
                <span class="ob-str" style="font-size:11px">${bb.desc}</span>
            </div>`).join("");
    } else {
        breakerSection.style.display = "none";
    }

    // FVG & CE
    const fvgCard = document.getElementById("fxFvgCard");
    let fvgHtml = "";
    if (data.fvg) {
        const fvg = data.fvg;
        if (fvg.bull > 0) fvgHtml += `<div class="fx-ms-row"><i class="fas fa-layer-group" style="color:var(--green)"></i><span class="label">Bullish FVG</span><span class="value" style="color:var(--green)">${fvg.bull} adet${fvg.ce_bull > 0 ? ' (' + fvg.ce_bull + ' CE test edildi)' : ''}</span></div>`;
        if (fvg.bear > 0) fvgHtml += `<div class="fx-ms-row"><i class="fas fa-layer-group" style="color:var(--red)"></i><span class="label">Bearish FVG</span><span class="value" style="color:var(--red)">${fvg.bear} adet${fvg.ce_bear > 0 ? ' (' + fvg.ce_bear + ' CE test edildi)' : ''}</span></div>`;
        if (fvg.active && fvg.active.length > 0) {
            for (const f of fvg.active.slice(0, 4)) {
                const fc = f.type.includes("BULL") ? "var(--green)" : "var(--red)";
                fvgHtml += `<div class="fx-ms-row"><i class="fas fa-arrow-right" style="color:${fc}"></i><span class="label">${f.type.replace(/_/g, " ")}</span><span class="value">${fmtPrice(f.bottom)} — ${fmtPrice(f.top)} | CE: ${fmtPrice(f.ce_level)}</span></div>`;
            }
        }
    }
    if (!fvgHtml) fvgHtml = `<div class="fx-no-item">Aktif FVG bulunamadı</div>`;
    fvgCard.innerHTML = fvgHtml;

    // Displacement & Liquidity
    const dispLiqCard = document.getElementById("fxDispLiqCard");
    let dlHtml = "";
    if (data.displacement && data.displacement.length > 0) {
        for (const d of data.displacement) {
            const dc = d.type.includes("BULL") ? "var(--green)" : "var(--red)";
            dlHtml += `<div class="fx-ms-row"><i class="fas fa-bolt-lightning" style="color:${dc}"></i><span class="label">Displacement</span><span class="value" style="color:${dc}">${d.desc}</span></div>`;
        }
    }
    if (data.liquidity_sweeps && data.liquidity_sweeps.length > 0) {
        for (const s of data.liquidity_sweeps) {
            const sc = s.type === "BUY_SIDE_SWEEP" ? "var(--green)" : "var(--red)";
            dlHtml += `<div class="fx-ms-row"><i class="fas fa-water" style="color:${sc}"></i><span class="label">Liquidity Sweep</span><span class="value" style="color:${sc}">${s.desc}</span></div>`;
        }
    }
    if (data.inducement && data.inducement.length > 0) {
        for (const ind of data.inducement) {
            const ic = ind.type.includes("BULL") ? "var(--green)" : "var(--red)";
            dlHtml += `<div class="fx-ms-row"><i class="fas fa-magnet" style="color:${ic}"></i><span class="label">Inducement</span><span class="value" style="color:${ic}">${ind.desc}</span></div>`;
        }
    }
    if (data.smart_money_trap) {
        const stc = data.smart_money_trap.type === "BEAR_TRAP" ? "var(--green)" : "var(--red)";
        dlHtml += `<div class="fx-ms-row"><i class="fas fa-skull-crossbones" style="color:${stc}"></i><span class="label">Smart Money Trap</span><span class="value" style="color:${stc}">${data.smart_money_trap.desc}</span></div>`;
    }
    if (!dlHtml) dlHtml = `<div class="fx-no-item">Displacement/Likidite aktivitesi yok</div>`;
    dispLiqCard.innerHTML = dlHtml;

    // Daily Bias & AMD & Judas
    const biasAmdCard = document.getElementById("fxBiasAmdCard");
    let baHtml = "";
    if (data.daily_bias) {
        const biasColor = data.daily_bias.bias === "BULLISH" ? "var(--green)" : data.daily_bias.bias === "BEARISH" ? "var(--red)" : "var(--yellow)";
        const biasIcon = data.daily_bias.bias === "BULLISH" ? "fa-arrow-trend-up" : data.daily_bias.bias === "BEARISH" ? "fa-arrow-trend-down" : "fa-arrows-left-right";
        baHtml += `<div class="fx-ms-row"><i class="fas ${biasIcon}" style="color:${biasColor}"></i><span class="label">Daily Bias (HTF)</span><span class="value" style="color:${biasColor}">${data.daily_bias.desc}</span></div>`;
    }
    if (data.amd) {
        const amdColor = data.amd.direction === "LONG" ? "var(--green)" : "var(--red)";
        baHtml += `<div class="fx-ms-row"><i class="fas fa-recycle" style="color:${amdColor}"></i><span class="label">Power of 3 (AMD)</span><span class="value" style="color:${amdColor}">${data.amd.desc}</span></div>`;
    }
    if (data.judas) {
        const jColor = data.judas.type.includes("BULL") ? "var(--green)" : "var(--red)";
        baHtml += `<div class="fx-ms-row"><i class="fas fa-masks-theater" style="color:${jColor}"></i><span class="label">Judas Swing</span><span class="value" style="color:${jColor}">${data.judas.desc}</span></div>`;
    }
    if (data.asian_breakout) {
        const abColor = data.asian_breakout.type === "BULLISH_BREAKOUT" ? "var(--green)" : data.asian_breakout.type === "BEARISH_BREAKOUT" ? "var(--red)" : "var(--yellow)";
        const abIcon = data.asian_breakout.type === "BULLISH_BREAKOUT" ? "fa-arrow-up" : data.asian_breakout.type === "BEARISH_BREAKOUT" ? "fa-arrow-down" : "fa-arrows-left-right";
        baHtml += `<div class="fx-ms-row"><i class="fas ${abIcon}" style="color:${abColor}"></i><span class="label">Asian Range Breakout</span><span class="value" style="color:${abColor}">${data.asian_breakout.desc}</span></div>`;
    }
    if (data.kill_zones && data.kill_zones.is_kill_zone) {
        baHtml += `<div class="fx-ms-row"><i class="fas fa-crosshairs" style="color:var(--yellow)"></i><span class="label">Kill Zone</span><span class="value" style="color:var(--yellow)">${data.kill_zones.desc}</span></div>`;
    }
    if (data.silver_bullet && data.silver_bullet.is_active) {
        baHtml += `<div class="fx-ms-row"><i class="fas fa-bullseye" style="color:var(--yellow)"></i><span class="label">Silver Bullet</span><span class="value" style="color:var(--yellow)">${data.silver_bullet.desc}</span></div>`;
    }
    if (!baHtml) baHtml = `<div class="fx-no-item">Aktif ICT pattern verisi yok</div>`;
    biasAmdCard.innerHTML = baHtml;

    // Premium/Discount
    const pdCard = document.getElementById("fxPdCard");
    const pd = data.premium_discount;
    const pdPct = pd.zone === "PREMIUM" ? 50 + pd.zone_pct / 2 : 50 - pd.zone_pct / 2;
    const pdColor = pd.zone === "DISCOUNT" ? "var(--green)" : "var(--red)";
    let pdHtml = `
        <div class="fx-pd-meter"><div class="fx-pd-pointer" style="left:${pdPct}%"></div></div>
        <div class="fx-pd-labels"><span style="color:var(--green)">Discount</span><span>Equilibrium: ${fmtPrice(pd.equilibrium)}</span><span style="color:var(--red)">Premium</span></div>
        <div class="fx-ms-row"><i class="fas fa-percentage" style="color:${pdColor}"></i><span class="label">Bölge</span><span class="value" style="color:${pdColor}">${pd.zone} (%${pd.zone_pct})</span></div>
        <div class="fx-ms-row"><i class="fas fa-arrows-up-down"></i><span class="label">Range</span><span class="value">${fmtPrice(pd.range_low)} — ${fmtPrice(pd.range_high)}</span></div>`;

    if (data.ote) {
        const oteColor = data.ote.direction === "LONG" ? "var(--green)" : "var(--red)";
        pdHtml += `<div class="fx-ms-row"><i class="fas fa-crosshairs" style="color:${oteColor}"></i><span class="label">OTE Bölgesi (Fib 0.618-0.786)</span><span class="value" style="color:${oteColor}">${data.ote.desc}</span></div>`;
    }
    pdCard.innerHTML = pdHtml;

    // Reasons
    const reasonsEl = document.getElementById("fxReasons");
    let reasonsHtml = "";
    for (const r of data.reasons_bull) {
        reasonsHtml += `<div class="fx-reason-item bull"><i class="fas fa-circle-check" style="color:var(--green);margin-right:6px"></i>${r}</div>`;
    }
    for (const r of data.reasons_bear) {
        reasonsHtml += `<div class="fx-reason-item bear"><i class="fas fa-circle-xmark" style="color:var(--red);margin-right:6px"></i>${r}</div>`;
    }
    if (!reasonsHtml) {
        reasonsHtml = `<div class="fx-no-item">Net bir sinyal gerekçesi yok</div>`;
    }
    reasonsEl.innerHTML = reasonsHtml;

    // Indicators
    const indEl = document.getElementById("fxIndicators");
    const ind = data.indicators;
    const rsiColor = ind.rsi > 70 ? 'var(--red)' : ind.rsi < 30 ? 'var(--green)' : 'var(--text-primary)';
    const rsiLabel = ind.rsi > 70 ? 'Aşırı Alım' : ind.rsi < 30 ? 'Aşırı Satım' : 'Normal';
    indEl.innerHTML = `
        <div class="fx-ind-item"><div class="fx-ind-label">RSI (14)</div><div class="fx-ind-val" style="color:${rsiColor}">${ind.rsi.toFixed(1)}</div><div class="fx-ind-sub">${rsiLabel}</div></div>
        <div class="fx-ind-item"><div class="fx-ind-label">EMA 20</div><div class="fx-ind-val">${fmtPrice(ind.ema20)}</div></div>
        <div class="fx-ind-item"><div class="fx-ind-label">EMA 50</div><div class="fx-ind-val">${fmtPrice(ind.ema50)}</div></div>
        ${ind.ema200 ? `<div class="fx-ind-item"><div class="fx-ind-label">EMA 200</div><div class="fx-ind-val">${fmtPrice(ind.ema200)}</div></div>` : ''}
        <div class="fx-ind-item"><div class="fx-ind-label">ATR (14)</div><div class="fx-ind-val">${fmtPrice(ind.atr)}</div></div>
        <div class="fx-ind-item"><div class="fx-ind-label">ATR %</div><div class="fx-ind-val">${ind.atr_pct}%</div></div>
    `;
}

function closeFxDetail() {
    document.getElementById("fxDetailPanel").style.display = "none";
    document.getElementById("fxSignalsGrid").style.display = "grid";
    const tfBar = document.querySelector(".fx-tf-bar");
    const sessionBar = document.getElementById("fxSessionBar");
    if (tfBar) tfBar.style.display = "flex";
    if (sessionBar) sessionBar.style.display = "flex";
}

// =================== ICT CHART ===================

let ictChart = null;
let ictCandleSeries = null;
let ictVolumeSeries = null;
let ictChartData = null;
let ictExtraSeriesList = [];
let ictResizeObserver = null;
let ictEma21Series = null;
let ictEma50Series = null;

function openICTChart(symbol) {
    const overlay = document.getElementById("ictChartOverlay");
    overlay.classList.add("active");
    document.getElementById("ictChartSymbol").textContent = symbol;
    document.getElementById("ictChartLoading").style.display = "flex";
    document.getElementById("ictChartContainer").innerHTML = "";

    // Eski chart'ı temizle
    if (ictChart) {
        ictChart.remove();
        ictChart = null;
    }
    if (ictResizeObserver) {
        ictResizeObserver.disconnect();
        ictResizeObserver = null;
    }
    ictExtraSeriesList = [];
    ictEma21Series = null;
    ictEma50Series = null;

    // Veri çek ve render et
    fetchICTChartData(symbol);
}

function closeICTChart(event) {
    if (event) event.stopPropagation();
    const overlay = document.getElementById("ictChartOverlay");
    overlay.classList.remove("active");
    if (ictChart) {
        ictChart.remove();
        ictChart = null;
    }
    if (ictResizeObserver) {
        ictResizeObserver.disconnect();
        ictResizeObserver = null;
    }
    ictExtraSeriesList = [];
    ictEma21Series = null;
    ictEma50Series = null;
}

async function fetchICTChartData(symbol) {
    try {
        const resp = await fetch(`/api/chart-data/${symbol}`);
        if (!resp.ok) throw new Error("API hatası");
        const data = await resp.json();
        if (data.error) throw new Error(data.error);

        ictChartData = data;
        renderICTChart(data);
    } catch (err) {
        console.error("ICT Chart hatası:", err);
        document.getElementById("ictChartLoading").style.display = "none";
        document.getElementById("ictChartContainer").innerHTML =
            `<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--red)">
                <i class="fas fa-exclamation-triangle" style="margin-right:8px"></i> Grafik yüklenemedi: ${err.message}
            </div>`;
    }
}

function renderICTChart(data) {
    const container = document.getElementById("ictChartContainer");
    container.innerHTML = "";

    // Chart oluştur
    ictChart = LightweightCharts.createChart(container, {
        width: container.clientWidth,
        height: container.clientHeight,
        layout: {
            background: { type: "solid", color: "#0d1117" },
            textColor: "#8b949e",
            fontFamily: "'Inter', sans-serif",
            fontSize: 11
        },
        grid: {
            vertLines: { color: "rgba(139,148,158,0.06)" },
            horzLines: { color: "rgba(139,148,158,0.06)" }
        },
        crosshair: {
            mode: LightweightCharts.CrosshairMode.Normal,
            vertLine: { color: "rgba(139,148,158,0.3)", style: 3, width: 1 },
            horzLine: { color: "rgba(139,148,158,0.3)", style: 3, width: 1 }
        },
        rightPriceScale: {
            borderColor: "rgba(139,148,158,0.15)",
            scaleMargins: { top: 0.05, bottom: 0.2 }
        },
        timeScale: {
            borderColor: "rgba(139,148,158,0.15)",
            timeVisible: true,
            secondsVisible: false,
            barSpacing: 8
        },
        handleScroll: { vertTouchDrag: false },
        handleScale: { axisPressedMouseMove: true }
    });

    // Candlestick serisi
    ictCandleSeries = ictChart.addCandlestickSeries({
        upColor: "#00d4aa",
        downColor: "#ff4757",
        borderUpColor: "#00d4aa",
        borderDownColor: "#ff4757",
        wickUpColor: "#00d4aa",
        wickDownColor: "#ff4757"
    });
    ictCandleSeries.setData(data.candles);

    // Volume serisi
    ictVolumeSeries = ictChart.addHistogramSeries({
        priceFormat: { type: "volume" },
        priceScaleId: "volume",
        scaleMargins: { top: 0.85, bottom: 0 }
    });
    ictChart.priceScale("volume").applyOptions({
        scaleMargins: { top: 0.85, bottom: 0 }
    });
    const volData = data.candles.map(c => ({
        time: c.time,
        value: c.volume,
        color: c.close >= c.open ? "rgba(0,212,170,0.15)" : "rgba(255,71,87,0.15)"
    }));
    ictVolumeSeries.setData(volData);

    // EMA çizgileri
    if (document.getElementById("toggleEMA").checked) {
        drawEMALines(data);
    }

    // Güncel fiyat çizgisi
    if (data.current_price) {
        const lastCandle = data.candles[data.candles.length - 1];
        const isUp = lastCandle ? lastCandle.close >= lastCandle.open : true;
        ictCandleSeries.createPriceLine({
            price: data.current_price,
            color: isUp ? "#00d4aa" : "#ff4757",
            lineWidth: 1,
            lineStyle: LightweightCharts.LineStyle.Solid,
            axisLabelVisible: true,
            title: ""
        });
    }

    // ICT katmanlarını çiz
    drawICTLayers(data);

    // Footer info güncelle
    updateICTFooter(data);

    // Header bias
    const biasEl = document.getElementById("ictChartBias");
    if (data.htf_bias) {
        biasEl.textContent = data.htf_bias;
        biasEl.className = "ict-chart-bias " + (data.htf_bias === "LONG" ? "bias-long" : "bias-short");
    } else {
        biasEl.textContent = "NEUTRAL";
        biasEl.className = "ict-chart-bias";
    }

    // Loading gizle
    document.getElementById("ictChartLoading").style.display = "none";

    // Resize observer (eski varsa temizle)
    if (ictResizeObserver) {
        ictResizeObserver.disconnect();
    }
    ictResizeObserver = new ResizeObserver(() => {
        if (ictChart) ictChart.applyOptions({ width: container.clientWidth, height: container.clientHeight });
    });
    ictResizeObserver.observe(container);

    // Toggle checkbox listeners
    setupICTToggleListeners();

    // OHLCV crosshair legend
    setupCrosshairLegend(data);

    // Son 50 muma zoom yap
    if (data.candles.length > 50) {
        const from = data.candles[data.candles.length - 50].time;
        const to = data.candles[data.candles.length - 1].time;
        ictChart.timeScale().setVisibleRange({ from, to });
    }
}

function drawEMALines(data) {
    // EMA 21 serisi
    if (data.ema_21 && data.ema_21.length > 0) {
        ictEma21Series = ictChart.addLineSeries({
            color: "rgba(255,193,7,0.7)",
            lineWidth: 1,
            lineStyle: LightweightCharts.LineStyle.Solid,
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
            title: ""
        });
        ictEma21Series.setData(data.ema_21);
    }
    // EMA 50 serisi
    if (data.ema_50 && data.ema_50.length > 0) {
        ictEma50Series = ictChart.addLineSeries({
            color: "rgba(0,188,212,0.7)",
            lineWidth: 1,
            lineStyle: LightweightCharts.LineStyle.Solid,
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
            title: ""
        });
        ictEma50Series.setData(data.ema_50);
    }
}

function setupCrosshairLegend(data) {
    if (!ictChart || !ictCandleSeries) return;

    const legendEl = document.getElementById("ictOHLCVLegend");
    if (!legendEl) return;

    // Son mum değerlerini başlangıçta göster
    if (data.candles.length > 0) {
        const last = data.candles[data.candles.length - 1];
        updateOHLCVDisplay(last, legendEl, data);
    }

    ictChart.subscribeCrosshairMove(param => {
        if (!param || !param.time) {
            // Crosshair chart dışındaysa son mum göster
            if (data.candles.length > 0) {
                updateOHLCVDisplay(data.candles[data.candles.length - 1], legendEl, data);
            }
            return;
        }

        const candleData = param.seriesData.get(ictCandleSeries);
        if (candleData) {
            updateOHLCVDisplay(candleData, legendEl, data);
        }
    });
}

function updateOHLCVDisplay(candle, legendEl, data) {
    if (!candle) return;

    const isUp = candle.close >= candle.open;
    const color = isUp ? "#00d4aa" : "#ff4757";
    const change = candle.open !== 0 ? ((candle.close - candle.open) / candle.open * 100).toFixed(2) : "0.00";

    // Fiyat format (küçük fiyatlar için daha fazla decimal)
    const priceDecimals = candle.close < 0.01 ? 6 : candle.close < 1 ? 4 : candle.close < 100 ? 3 : 2;
    const fmt = (v) => v !== undefined ? Number(v).toFixed(priceDecimals) : "---";
    const volFmt = (v) => {
        if (!v) return "0";
        if (v >= 1e9) return (v / 1e9).toFixed(1) + "B";
        if (v >= 1e6) return (v / 1e6).toFixed(1) + "M";
        if (v >= 1e3) return (v / 1e3).toFixed(1) + "K";
        return v.toFixed(0);
    };

    // Eşleşen candle'ından volume al
    let vol = candle.volume;
    if (vol === undefined && data && data.candles) {
        const match = data.candles.find(c => c.time === candle.time);
        if (match) vol = match.volume;
    }

    // EMA değerleri
    let ema21Val = "", ema50Val = "";
    if (data && data.ema_21) {
        const e21 = data.ema_21.find(e => e.time === candle.time);
        if (e21) ema21Val = `<span style="color:rgba(255,193,7,0.9)">EMA21: ${fmt(e21.value)}</span>`;
    }
    if (data && data.ema_50) {
        const e50 = data.ema_50.find(e => e.time === candle.time);
        if (e50) ema50Val = `<span style="color:rgba(0,188,212,0.9)">EMA50: ${fmt(e50.value)}</span>`;
    }

    legendEl.innerHTML =
        `<span style="color:${color}">O: ${fmt(candle.open)}</span>` +
        `<span style="color:${color}">H: ${fmt(candle.high)}</span>` +
        `<span style="color:${color}">L: ${fmt(candle.low)}</span>` +
        `<span style="color:${color}">C: ${fmt(candle.close)}</span>` +
        `<span style="color:${color}">${change}%</span>` +
        `<span style="color:#8b949e">Vol: ${volFmt(vol)}</span>` +
        (ema21Val ? ema21Val : "") +
        (ema50Val ? ema50Val : "");
}

function drawICTLayers(data) {
    if (!ictCandleSeries || !ictChart) return;

    const markers = [];

    // 1) FVG bölgeleri
    if (document.getElementById("toggleFVG").checked && data.fvgs && data.fvgs.length > 0) {
        data.fvgs.forEach((fvg, i) => {
            const isBullish = fvg.type === "BULLISH";
            const borderColor = isBullish ? "rgba(155,89,182,0.60)" : "rgba(231,76,60,0.60)";

            ictCandleSeries.createPriceLine({
                price: fvg.high,
                color: borderColor,
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Dotted,
                axisLabelVisible: false,
                title: ""
            });
            ictCandleSeries.createPriceLine({
                price: fvg.low,
                color: borderColor,
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Dotted,
                axisLabelVisible: false,
                title: ""
            });

            markers.push({
                time: fvg.time,
                position: isBullish ? "belowBar" : "aboveBar",
                color: isBullish ? "#9b59b6" : "#e74c3c",
                shape: "square",
                text: `FVG ${isBullish ? "▲" : "▼"}`,
                _priority: 2
            });
        });
    }

    // 2) Order Blocks
    if (document.getElementById("toggleOB").checked && data.order_blocks && data.order_blocks.length > 0) {
        data.order_blocks.forEach((ob) => {
            const isBullish = ob.type === "BULLISH";
            const color = isBullish ? "rgba(52,152,219,0.50)" : "rgba(230,126,34,0.50)";

            ictCandleSeries.createPriceLine({
                price: ob.high,
                color: color,
                lineWidth: 2,
                lineStyle: LightweightCharts.LineStyle.Solid,
                axisLabelVisible: false,
                title: ""
            });
            ictCandleSeries.createPriceLine({
                price: ob.low,
                color: color,
                lineWidth: 2,
                lineStyle: LightweightCharts.LineStyle.Solid,
                axisLabelVisible: false,
                title: ""
            });

            markers.push({
                time: ob.time,
                position: isBullish ? "belowBar" : "aboveBar",
                color: isBullish ? "#3498db" : "#e67e22",
                shape: "square",
                text: isBullish ? "Bull OB" : "Bear OB",
                _priority: 3
            });
        });
    }

    // 3) BOS events
    if (document.getElementById("toggleBOS").checked && data.bos_events && data.bos_events.length > 0) {
        data.bos_events.forEach(bos => {
            const isBullish = bos.type.includes("BULLISH");
            markers.push({
                time: bos.time,
                position: isBullish ? "aboveBar" : "belowBar",
                color: isBullish ? "#2ecc71" : "#e74c3c",
                shape: isBullish ? "arrowUp" : "arrowDown",
                text: "BOS",
                _priority: 4
            });

            ictCandleSeries.createPriceLine({
                price: bos.price,
                color: isBullish ? "rgba(46,204,113,0.5)" : "rgba(231,76,60,0.5)",
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Dashed,
                axisLabelVisible: false,
                title: "BOS"
            });
        });
    }

    // 4) CHoCH events
    if (document.getElementById("toggleCHoCH").checked && data.choch_events && data.choch_events.length > 0) {
        data.choch_events.forEach(ch => {
            const isBullish = ch.type.includes("BULLISH");
            markers.push({
                time: ch.time,
                position: isBullish ? "aboveBar" : "belowBar",
                color: "#f1c40f",
                shape: "circle",
                text: "CHoCH",
                _priority: 5
            });

            ictCandleSeries.createPriceLine({
                price: ch.price,
                color: "rgba(241,196,15,0.6)",
                lineWidth: 2,
                lineStyle: LightweightCharts.LineStyle.SparseDotted,
                axisLabelVisible: false,
                title: "CHoCH"
            });
        });
    }

    // 5) Sweep event
    if (document.getElementById("toggleSweep").checked && data.sweep) {
        const sw = data.sweep;
        markers.push({
            time: sw.time,
            position: sw.type === "SSL_SWEEP" ? "belowBar" : "aboveBar",
            color: "#e74c3c",
            shape: sw.type === "SSL_SWEEP" ? "arrowDown" : "arrowUp",
            text: `🔥 ${sw.type === "SSL_SWEEP" ? "SSL" : "BSL"} Sweep`,
            _priority: 6
        });

        ictCandleSeries.createPriceLine({
            price: sw.swept_level,
            color: "rgba(231,76,60,0.8)",
            lineWidth: 2,
            lineStyle: LightweightCharts.LineStyle.Dashed,
            axisLabelVisible: true,
            title: "Sweep Level"
        });
    }

    // 6) Premium/Discount zones
    if (document.getElementById("togglePD").checked && data.premium_discount) {
        const pd = data.premium_discount;

        ictCandleSeries.createPriceLine({
            price: pd.equilibrium,
            color: "rgba(149,165,166,0.7)",
            lineWidth: 2,
            lineStyle: LightweightCharts.LineStyle.LargeDashed,
            axisLabelVisible: true,
            title: "EQ"
        });

        if (pd.ote_high && pd.ote_low) {
            ictCandleSeries.createPriceLine({
                price: pd.ote_high,
                color: "rgba(155,89,182,0.5)",
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Dotted,
                axisLabelVisible: false,
                title: "OTE H"
            });
            ictCandleSeries.createPriceLine({
                price: pd.ote_low,
                color: "rgba(155,89,182,0.5)",
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Dotted,
                axisLabelVisible: false,
                title: "OTE L"
            });
        }

        ictCandleSeries.createPriceLine({
            price: pd.high,
            color: "rgba(231,76,60,0.3)",
            lineWidth: 1,
            lineStyle: LightweightCharts.LineStyle.Dotted,
            axisLabelVisible: false,
            title: "Premium"
        });
        ictCandleSeries.createPriceLine({
            price: pd.low,
            color: "rgba(46,204,113,0.3)",
            lineWidth: 1,
            lineStyle: LightweightCharts.LineStyle.Dotted,
            axisLabelVisible: false,
            title: "Discount"
        });
    }

    // 7) Swing Highs/Lows
    if (document.getElementById("toggleSwing").checked) {
        if (data.swing_highs) {
            data.swing_highs.forEach(sh => {
                markers.push({
                    time: sh.time,
                    position: "aboveBar",
                    color: "rgba(189,195,199,0.7)",
                    shape: "arrowDown",
                    text: "HH",
                    _priority: 1
                });
            });
        }
        if (data.swing_lows) {
            data.swing_lows.forEach(sl => {
                markers.push({
                    time: sl.time,
                    position: "belowBar",
                    color: "rgba(189,195,199,0.7)",
                    shape: "arrowUp",
                    text: "LL",
                    _priority: 1
                });
            });
        }
    }

    // 8) Active signal lines (Entry / SL / TP)
    if (document.getElementById("toggleSignal").checked && data.active_signal) {
        const sig = data.active_signal;

        ictCandleSeries.createPriceLine({
            price: sig.entry,
            color: "#00d4aa",
            lineWidth: 2,
            lineStyle: LightweightCharts.LineStyle.Dashed,
            axisLabelVisible: true,
            title: `▶ Entry (${sig.direction})`
        });

        ictCandleSeries.createPriceLine({
            price: sig.sl,
            color: "#ff4757",
            lineWidth: 2,
            lineStyle: LightweightCharts.LineStyle.Dashed,
            axisLabelVisible: true,
            title: "✕ Stop Loss"
        });

        ictCandleSeries.createPriceLine({
            price: sig.tp,
            color: "#3498db",
            lineWidth: 2,
            lineStyle: LightweightCharts.LineStyle.Dashed,
            axisLabelVisible: true,
            title: "★ Take Profit"
        });

        // R:R oranı hesapla ve göster
        if (sig.entry && sig.sl && sig.tp) {
            const risk = Math.abs(sig.entry - sig.sl);
            const reward = Math.abs(sig.tp - sig.entry);
            const rr = risk > 0 ? (reward / risk).toFixed(1) : "∞";
            const rrEl = document.getElementById("ictInfoRR");
            if (rrEl) {
                rrEl.textContent = `1:${rr}`;
                rrEl.style.color = parseFloat(rr) >= 2 ? "#00d4aa" : parseFloat(rr) >= 1 ? "#f1c40f" : "#ff4757";
            }
        }
    }

    // 9) Liquidity levels
    if (document.getElementById("toggleLiquidity").checked && data.liquidity_levels && data.liquidity_levels.length > 0) {
        data.liquidity_levels.forEach(liq => {
            const isHighs = liq.type === "EQUAL_HIGHS";
            ictCandleSeries.createPriceLine({
                price: liq.price,
                color: liq.swept ? "rgba(149,165,166,0.3)" : (isHighs ? "rgba(231,76,60,0.5)" : "rgba(46,204,113,0.5)"),
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.SparseDotted,
                axisLabelVisible: false,
                title: liq.swept ? `${liq.type} ✓` : `${liq.type} (${liq.touches}x)`
            });
        });
    }

    // 10) Breaker blocks
    if (document.getElementById("toggleBreaker").checked && data.breaker_blocks && data.breaker_blocks.length > 0) {
        data.breaker_blocks.forEach(bb => {
            const isBullish = bb.type === "BULLISH";
            ictCandleSeries.createPriceLine({
                price: bb.high,
                color: isBullish ? "rgba(26,188,156,0.4)" : "rgba(192,57,43,0.4)",
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Solid,
                axisLabelVisible: false,
                title: ""
            });
            ictCandleSeries.createPriceLine({
                price: bb.low,
                color: isBullish ? "rgba(26,188,156,0.4)" : "rgba(192,57,43,0.4)",
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Solid,
                axisLabelVisible: false,
                title: ""
            });
            markers.push({
                time: bb.time,
                position: isBullish ? "belowBar" : "aboveBar",
                color: isBullish ? "#1abc9c" : "#c0392b",
                shape: "diamond",
                text: "BB",
                _priority: 2
            });
        });
    }

    // 11) Displacement candles
    if (document.getElementById("toggleDisplacement").checked && data.displacements && data.displacements.length > 0) {
        data.displacements.forEach(d => {
            markers.push({
                time: d.time,
                position: d.direction === "BULLISH" ? "belowBar" : "aboveBar",
                color: "#f39c12",
                shape: "circle",
                text: "⚡",
                _priority: 1
            });
        });
    }

    // Marker'ları time sırasına göre sırala
    if (markers.length > 0) {
        markers.sort((a, b) => a.time - b.time);

        // Aynı time+position'da birden fazla marker varsa textlerini birleştir (en yüksek priority olanı ana shape olarak kullan)
        const grouped = {};
        for (const m of markers) {
            const key = `${m.time}-${m.position}`;
            if (!grouped[key]) {
                grouped[key] = { ...m };
            } else {
                // Text birleştir
                grouped[key].text += ` + ${m.text}`;
                // Daha yüksek priority olan şekli kullan (büyük sayı = daha önemli)
                if ((m._priority || 0) > (grouped[key]._priority || 0)) {
                    grouped[key].shape = m.shape;
                    grouped[key].color = m.color;
                    grouped[key]._priority = m._priority;
                }
            }
        }
        const uniqueMarkers = Object.values(grouped).map(m => {
            const { _priority, ...clean } = m;
            return clean;
        });
        uniqueMarkers.sort((a, b) => a.time - b.time);
        ictCandleSeries.setMarkers(uniqueMarkers);
    }
}

function updateICTFooter(data) {
    document.getElementById("ictInfoTrend").textContent = data.ltf_trend || "---";
    document.getElementById("ictInfoBias").textContent = data.htf_bias || "---";
    document.getElementById("ictInfoFVG").textContent = data.fvgs ? data.fvgs.length : 0;
    document.getElementById("ictInfoOB").textContent = data.order_blocks ? data.order_blocks.length : 0;
    document.getElementById("ictInfoSweep").textContent = data.sweep ? data.sweep.type : "Yok";

    const zone = data.premium_discount ? data.premium_discount.zone : "---";
    const zoneEl = document.getElementById("ictInfoZone");
    zoneEl.textContent = zone;
    zoneEl.style.color = zone === "PREMIUM" ? "var(--red)" : zone === "DISCOUNT" ? "var(--green)" : "var(--text-secondary)";

    const trendEl = document.getElementById("ictInfoTrend");
    const trend = data.ltf_trend || "";
    trendEl.style.color = trend.includes("BULL") ? "var(--green)" : trend.includes("BEAR") ? "var(--red)" : "var(--text-secondary)";

    const biasEl = document.getElementById("ictInfoBias");
    biasEl.style.color = data.htf_bias === "LONG" ? "var(--green)" : data.htf_bias === "SHORT" ? "var(--red)" : "var(--text-secondary)";

    // Ekstra footer bilgileri
    const dispEl = document.getElementById("ictInfoDisp");
    if (dispEl) dispEl.textContent = data.displacements ? data.displacements.length : 0;

    const liqEl = document.getElementById("ictInfoLiq");
    if (liqEl) liqEl.textContent = data.liquidity_levels ? data.liquidity_levels.length : 0;

    const bbEl = document.getElementById("ictInfoBB");
    if (bbEl) bbEl.textContent = data.breaker_blocks ? data.breaker_blocks.length : 0;

    const bosEl = document.getElementById("ictInfoBOS");
    if (bosEl) bosEl.textContent = data.bos_count || 0;

    const chochEl = document.getElementById("ictInfoCHoCH");
    if (chochEl) chochEl.textContent = data.structure_shift_count || 0;

    // R:R başlangıç
    const rrEl = document.getElementById("ictInfoRR");
    if (rrEl && data.active_signal) {
        const sig = data.active_signal;
        const risk = Math.abs(sig.entry - sig.sl);
        const reward = Math.abs(sig.tp - sig.entry);
        const rr = risk > 0 ? (reward / risk).toFixed(1) : "∞";
        rrEl.textContent = `1:${rr}`;
        rrEl.style.color = parseFloat(rr) >= 2 ? "#00d4aa" : parseFloat(rr) >= 1 ? "#f1c40f" : "#ff4757";
    } else if (rrEl) {
        rrEl.textContent = "---";
        rrEl.style.color = "var(--text-secondary)";
    }

    // Confidence
    const confEl = document.getElementById("ictInfoConf");
    if (confEl && data.active_signal && data.active_signal.confidence) {
        const conf = data.active_signal.confidence;
        confEl.textContent = `${conf}%`;
        confEl.style.color = conf >= 70 ? "#00d4aa" : conf >= 50 ? "#f1c40f" : "#ff4757";
    } else if (confEl) {
        confEl.textContent = "---";
        confEl.style.color = "var(--text-secondary)";
    }
}

function setupICTToggleListeners() {
    const toggleIds = [
        "toggleFVG", "toggleOB", "toggleBOS", "toggleCHoCH", "toggleSweep",
        "togglePD", "toggleSwing", "toggleSignal", "toggleEMA",
        "toggleLiquidity", "toggleBreaker", "toggleDisplacement"
    ];
    toggleIds.forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.onchange = () => {
                if (ictChartData && ictCandleSeries) {
                    redrawICTChart();
                }
            };
        }
    });
}

function redrawICTChart() {
    if (!ictChartData || !ictChart) return;

    // Mevcut visible range'i kaydet
    const visibleRange = ictChart.timeScale().getVisibleRange();

    // Chart'ı tamamen yeniden render et
    const container = document.getElementById("ictChartContainer");
    ictChart.remove();
    ictChart = null;
    ictExtraSeriesList = [];
    ictEma21Series = null;
    ictEma50Series = null;

    renderICTChart(ictChartData);

    // Önceki zoom seviyesini geri yükle
    if (visibleRange) {
        setTimeout(() => {
            if (ictChart) ictChart.timeScale().setVisibleRange(visibleRange);
        }, 50);
    }
}

// Escape tuşu ile kapat
document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
        const overlay = document.getElementById("ictChartOverlay");
        if (overlay && overlay.classList.contains("active")) {
            closeICTChart(e);
        }
    }
});


// =================== AME (Adaptive Microstructure Engine) ===================

// ═══════════════════════════════════════════════════════
// AME — Adaptive Microstructure Engine Frontend
// ═══════════════════════════════════════════════════════

let _ameAnalysisCache = null;

async function loadAME() {
    try {
        const [activeData, historyData, perfData, statusData] = await Promise.all([
            apiFetch("/api/ame/signals/active"),
            apiFetch("/api/ame/signals/history?limit=30"),
            apiFetch("/api/ame/performance"),
            apiFetch("/api/ame/status"),
        ]);

        // ── Performance stats ──
        if (perfData) {
            setText("ameTotalTrades", perfData.total_trades || 0);
            setText("ameWinRate", perfData.total_trades > 0 ? perfData.win_rate + "%" : "—");
            setText("ameAvgRR", perfData.avg_rr ? parseFloat(perfData.avg_rr).toFixed(1) : "—");
            const pnl = perfData.total_pnl || 0;
            const pnlEl = document.getElementById("ameNetPnL");
            if (pnlEl) {
                pnlEl.textContent = (pnl >= 0 ? "+" : "") + pnl.toFixed(2) + "%";
                pnlEl.style.color = pnl >= 0 ? "var(--green)" : "var(--red)";
            }
            const iconEl = document.getElementById("amePnlIcon");
            if (iconEl) {
                iconEl.style.background = pnl >= 0 ? "var(--green-dim)" : "var(--red-dim)";
                iconEl.style.color = pnl >= 0 ? "var(--green)" : "var(--red)";
            }
            setText("ameAvgScore", perfData.avg_score ? parseFloat(perfData.avg_score).toFixed(0) : "—");
        }

        // ── Status / Mode ──
        if (statusData) {
            setText("ameActiveTrades", statusData.active_trades || 0);
            const sel = document.getElementById("ameModeSelect");
            if (sel) sel.value = statusData.strategy_mode || "balanced";

            // Active count badge
            const countBadge = document.getElementById("ameActiveCount");
            if (countBadge) countBadge.textContent = activeData ? activeData.length : 0;

            // Nav badge
            const badge = document.getElementById("navBadgeAME");
            if (badge) {
                const count = activeData ? activeData.length : 0;
                badge.textContent = count > 0 ? count : "";
                badge.style.display = count > 0 ? "inline-flex" : "none";
            }

            // ── Scan status bar ──
            const ls = statusData.last_scan;
            if (ls) {
                const scanDot = document.getElementById("ameScanDot");
                const scanText = document.getElementById("ameScanText");
                if (ls.scanning) {
                    scanDot && scanDot.classList.add("scanning");
                    scanText && (scanText.textContent = "Tarama devam ediyor...");
                } else if (ls.time) {
                    scanDot && scanDot.classList.remove("scanning");
                    const ago = getElapsed(ls.time);
                    scanText && (scanText.textContent = `Son tarama: ${ago} önce`);
                }
                setText("ameScanCoins", ls.coins || "—");
                setText("ameScanCandidates", ls.candidates || "—");
                setText("ameScanSignals", ls.signals || 0);
            }

            // ── BTC filter / risk engine card (mode display) ──
            const modeLabels = { aggressive: "🔥 Agresif", balanced: "⚖️ Dengeli", conservative: "🛡️ Konservatif" };
            const p = statusData.params || {};
            const riskDetail = `TP: ${p.tp1_rr || 1}R/${p.tp2_rr || 2}R/${p.tp3_rr || 3.5}R · Max: ${p.max_concurrent || 5}`;
            const riskDetailEl = document.getElementById("ameRiskDetail");
            // Only set if not already populated by analysis
            if (riskDetailEl && riskDetailEl.textContent.startsWith("Corr: —")) {
                riskDetailEl.textContent = riskDetail;
            }

            // ── Kelly + Adaptive Badge ──
            const kelly = statusData.kelly || {};
            const adaptive = statusData.adaptive || {};
            const kellyBadge = document.getElementById("ameKellyBadge");
            if (kellyBadge) {
                if (kelly.sufficient_data) {
                    kellyBadge.textContent = `Kelly: ${kelly.position_pct}% | f*=${kelly.kelly_full}`;
                    kellyBadge.style.color = "var(--cyan)";
                } else {
                    kellyBadge.textContent = `Kelly: Veri yetersiz (default ${p.default_position_pct || 2}%)`;
                    kellyBadge.style.color = "var(--text-muted)";
                }
            }
            const adaptBadge = document.getElementById("ameAdaptBadge");
            if (adaptBadge) {
                if (adaptive.enabled) {
                    adaptBadge.textContent = `Adaptif: MinScore=${adaptive.min_signal_score} · MaxPos=${adaptive.max_concurrent} · Cooldown=${adaptive.cooldown_minutes}dk`;
                    adaptBadge.style.color = "var(--orange)";
                } else {
                    adaptBadge.textContent = "Adaptif: Kapalı";
                    adaptBadge.style.color = "var(--text-muted)";
                }
            }
        }

        // ── Tables (use enriched active signals from status if available) ──
        const enrichedActive = statusData && statusData.active_signals ? statusData.active_signals : (activeData || []);
        renderAMEActiveTable(enrichedActive);
        renderAMEHistoryTable(historyData || []);

        // ── Load BTC analysis for engine components ──
        loadAMEAnalysis();

    } catch (e) {
        console.error("AME load error:", e);
    }
}

async function loadAMEAnalysis() {
    try {
        const data = await apiFetch("/api/ame/analyze/BTC-USDT-SWAP");
        if (!data || !data.analysis) return;
        const a = data.analysis;
        _ameAnalysisCache = a;

        // ── 1. Hurst Regime Engine ──
        const regime = a.regime || {};
        const regimeType = regime.regime || "UNKNOWN";
        const regimeColors = {
            TREND_BULLISH: "var(--green)", TREND_BULL: "var(--green)", TREND_BEARISH: "var(--red)", TREND_BEAR: "var(--red)",
            MEAN_REVERT: "var(--yellow)", TRANSITION: "var(--orange)", CHAOS: "var(--red)", UNKNOWN: "var(--text-muted)"
        };
        const regimeLabels = {
            TREND_BULLISH: "📈 Trend Bull", TREND_BULL: "📈 Trend Bull", TREND_BEARISH: "📉 Trend Bear", TREND_BEAR: "📉 Trend Bear",
            MEAN_REVERT: "🔄 Mean Revert", TRANSITION: "⚡ Transition", CHAOS: "🌀 Chaos", UNKNOWN: "◆ Bilinmiyor"
        };
        setText("ameVolRegime", regimeLabels[regimeType] || regimeType);
        const volEl = document.getElementById("ameVolRegime");
        if (volEl) volEl.style.color = regimeColors[regimeType] || "var(--text-secondary)";
        const hurst = regime.hurst || 0.5;
        const efficiency = regime.efficiency || 0.5;
        setText("ameVolDetail", `H: ${hurst.toFixed(3)} · E: ${efficiency.toFixed(3)}`);
        const volBar = document.getElementById("ameVolBar");
        if (volBar) volBar.style.width = Math.min(100, hurst * 100) + "%";

        // ── 2. Order Flow Engine ──
        const of = a.orderflow || {};
        const ofBias = of.bias || "NEUTRAL";
        const biasColors = { LONG: "var(--green)", SHORT: "var(--red)", NEUTRAL: "var(--text-muted)" };
        const bLabel = ofBias === "LONG" ? "▲ Long Bias" : ofBias === "SHORT" ? "▼ Short Bias" : "◆ Nötr";
        setText("ameImpBias", bLabel);
        const impEl = document.getElementById("ameImpBias");
        if (impEl) impEl.style.color = biasColors[ofBias] || "var(--text-secondary)";
        const cvdDiv = of.cvd ? of.cvd.divergence || "NONE" : "NONE";
        const sweepType = of.sweep ? of.sweep.type || "—" : "—";
        const cvdLabel = cvdDiv === "CONFIRMING" ? "✓ Confirm" : cvdDiv.includes("DIVERGENCE") ? "⚠ Div" : "— Nötr";
        const sweepLabel = sweepType.includes("BULLISH") ? "▲ Sweep" : sweepType.includes("BEARISH") ? "▼ Sweep" : "—";
        setText("ameImpDetail", `CVD: ${cvdLabel} · Sweep: ${sweepLabel}`);
        const impBar = document.getElementById("ameImpBar");
        if (impBar) impBar.style.width = Math.min(100, (of.strength || 0) * 100) + "%";

        // ── 3. Multi-TF Confluence ──
        const conf = a.confluence || {};
        const confDir = conf.direction || "NEUTRAL";
        const confCount = conf.count || 0;
        const confColors = { LONG: "var(--green)", SHORT: "var(--red)", NEUTRAL: "var(--text-muted)" };
        const confLabel = confDir === "LONG" ? `▲ LONG (${confCount}/3)` : confDir === "SHORT" ? `▼ SHORT (${confCount}/3)` : `◆ Nötr (${confCount}/3)`;
        setText("ameAccSignal", confLabel);
        const accEl = document.getElementById("ameAccSignal");
        if (accEl) accEl.style.color = confColors[confDir] || "var(--text-secondary)";
        const tf4h = conf.tf_4h ? conf.tf_4h.direction || "—" : "—";
        const tf1h = conf.tf_1h ? conf.tf_1h.direction || "—" : "—";
        const tf15m = conf.tf_15m ? conf.tf_15m.direction || "—" : "—";
        setText("ameAccDetail", `4H: ${tf4h.substring(0,4)} · 1H: ${tf1h.substring(0,4)} · 15M: ${tf15m.substring(0,4)}`);
        const accBar = document.getElementById("ameAccBar");
        if (accBar) accBar.style.width = Math.min(100, confCount / 3 * 100) + "%";

        // ── 4. BTC Filter + Risk ──
        const btc = a.btc || {};
        const btcCorr = btc.correlation || 0;
        const btcTrend = btc.trend ? btc.trend.direction || "NEUTRAL" : "NEUTRAL";
        const btcLabel = btcTrend === "BULLISH" ? "BTC ▲" : btcTrend === "BEARISH" ? "BTC ▼" : "BTC ◆";
        const session = a.session || "—";
        setText("ameRiskMode", `${btcLabel} · r=${btcCorr.toFixed(2)}`);
        const riskEl = document.getElementById("ameRiskMode");
        if (riskEl) riskEl.style.color = btcCorr > 0.7 ? "var(--yellow)" : "var(--green)";
        setText("ameRiskDetail", `Corr: ${(btcCorr * 100).toFixed(0)}% · Session: ${session}`);
        const riskBar = document.getElementById("ameRiskBar");
        if (riskBar) riskBar.style.width = Math.min(100, Math.abs(btcCorr) * 100) + "%";

        // ── 5. Likidite Havuzu (v2.1) ──
        const liq = a.liquidity || {};
        const sweepRisk = liq.sweep_risk || "LOW";
        const eqhCount = liq.equal_highs ? liq.equal_highs.length : 0;
        const eqlCount = liq.equal_lows ? liq.equal_lows.length : 0;
        const liqColors = { HIGH: "#e74c3c", MEDIUM: "#f39c12", LOW: "#2ecc71" };
        const liqLabels = { HIGH: "🔴 Yüksek Risk", MEDIUM: "🟡 Orta Risk", LOW: "🟢 Düşük Risk" };
        setText("ameLiqRisk", liqLabels[sweepRisk] || "—");
        const liqValEl = document.getElementById("ameLiqRisk");
        if (liqValEl) liqValEl.style.color = liqColors[sweepRisk] || "#888";
        setText("ameLiqDetail", `EQH: ${eqhCount} · EQL: ${eqlCount} · Sweep: ${sweepRisk}`);
        const liqBar = document.getElementById("ameLiqBar");
        if (liqBar) liqBar.style.width = sweepRisk === "HIGH" ? "90%" : sweepRisk === "MEDIUM" ? "55%" : "20%";

        // ── 6. Volume Profile (v2.1) ──
        const vp = a.volume_profile || {};
        const vpPos = vp.position || "UNKNOWN";
        const vpoc = vp.vpoc || 0;
        const vah = vp.vah || 0;
        const val_ = vp.val || 0;
        const vpColors = { ABOVE_VA: "#e74c3c", BELOW_VA: "#2ecc71", INSIDE_VA: "#9b59b6", AT_VPOC: "#f1c40f" };
        const vpLabels = { ABOVE_VA: "▲ VA Üstü", BELOW_VA: "▼ VA Altı", INSIDE_VA: "◆ VA İçi", AT_VPOC: "⊕ VPOC'ta" };
        setText("ameVpocPos", vpLabels[vpPos] || vpPos);
        const vpValEl = document.getElementById("ameVpocPos");
        if (vpValEl) vpValEl.style.color = vpColors[vpPos] || "#888";
        const fmtP = (v) => v > 1000 ? v.toFixed(1) : v > 1 ? v.toFixed(4) : v.toFixed(6);
        setText("ameVpocDetail", `VPOC: ${fmtP(vpoc)} · VAH: ${fmtP(vah)} · VAL: ${fmtP(val_)}`);
        const vpBar = document.getElementById("ameVpocBar");
        if (vpBar) vpBar.style.width = vpPos === "AT_VPOC" ? "90%" : vpPos === "INSIDE_VA" ? "55%" : "30%";

        // ── 7. Funding + OI (v2.1) ──
        const fund = a.funding_oi || {};
        const fundSent = fund.sentiment || "NEUTRAL";
        const fundRate = fund.funding_rate || 0;
        const fundOi = fund.oi_direction || "—";
        const sqRisk = fund.squeeze_risk || false;
        const fundColors = { BULLISH: "#2ecc71", BEARISH: "#e74c3c", NEUTRAL: "#888", EXTREME_LONG: "#e74c3c", EXTREME_SHORT: "#2ecc71" };
        const fundLabels = { BULLISH: "▲ Bullish", BEARISH: "▼ Bearish", NEUTRAL: "◆ Nötr", EXTREME_LONG: "🔴 Aşırı Long", EXTREME_SHORT: "🟢 Aşırı Short" };
        setText("ameFundSent", fundLabels[fundSent] || fundSent);
        const fundValEl = document.getElementById("ameFundSent");
        if (fundValEl) fundValEl.style.color = fundColors[fundSent] || "#888";
        setText("ameFundDetail", `FR: ${(fundRate * 100).toFixed(4)}% · OI: ${fundOi} · Squeeze: ${sqRisk ? "⚠ Evet" : "Hayır"}`);
        const fundBar = document.getElementById("ameFundBar");
        if (fundBar) fundBar.style.width = Math.min(100, Math.abs(fundRate) * 10000) + "%";

        // ── 8. Wyckoff Faz (v2.1) ──
        const wyck = a.wyckoff || {};
        const wyckPhase = wyck.phase || "NONE";
        const wyckSignal = wyck.signal || "NONE";
        const wyckStr = wyck.strength || 0;
        const wyckColors = { ACCUMULATION: "#2ecc71", DISTRIBUTION: "#e74c3c", MARKUP: "#27ae60", MARKDOWN: "#c0392b", NONE: "#888" };
        const wyckLabels = { ACCUMULATION: "📦 Birikim", DISTRIBUTION: "📤 Dağıtım", MARKUP: "🚀 Markup", MARKDOWN: "📉 Markdown", NONE: "◆ Belirsiz" };
        setText("ameWyckPhase", wyckLabels[wyckPhase] || wyckPhase);
        const wyckValEl = document.getElementById("ameWyckPhase");
        if (wyckValEl) wyckValEl.style.color = wyckColors[wyckPhase] || "#888";
        const wyckSigLabel = wyckSignal === "SPRING" ? "🔺 Spring" : wyckSignal === "UPTHRUST" ? "🔻 Upthrust" : "—";
        setText("ameWyckDetail", `Sinyal: ${wyckSigLabel} · Güç: ${(wyckStr * 100).toFixed(0)}%`);
        const wyckBar = document.getElementById("ameWyckBar");
        if (wyckBar) wyckBar.style.width = Math.min(100, wyckStr * 100) + "%";

        // ── Overall score ──
        setText("ameScoreVal", a.score ? a.score.toFixed(0) : "0");

    } catch (e) {
        console.error("AME analysis error:", e);
    }
}

function renderAMEActiveTable(signals) {
    const tbody = document.getElementById("ameActiveTable");
    if (!tbody) return;

    if (!signals || signals.length === 0) {
        tbody.innerHTML = `<tr><td colspan="10" class="ame-empty-state">
            <div class="ame-empty-icon"><i class="fas fa-atom fa-spin-slow"></i></div>
            <div class="ame-empty-title">Aktif sinyal yok</div>
            <div class="ame-empty-desc">Motor çalışıyor — Multi-TF confluence bekleniyor</div>
        </td></tr>`;
        return;
    }

    tbody.innerHTML = signals.map(s => {
        const dir = s.direction === "LONG"
            ? '<span class="ame-dir long">▲ LONG</span>'
            : '<span class="ame-dir short">▼ SHORT</span>';
        const elapsed = s.created_at ? getElapsed(s.created_at) : "—";
        const regCls = (s.regime || "").toLowerCase().replace("_", "-");
        const tp1Hit = s.tp1_hit ? "✓" : "·";
        const tp2Hit = s.tp2_hit ? "✓" : "·";
        const tp1 = s.tp1 ? fmtPrice(s.tp1) : "—";
        const tp2 = s.tp2 ? fmtPrice(s.tp2) : "—";
        const tp3 = s.tp3 ? fmtPrice(s.tp3) : fmtPrice(s.take_profit);
        const tpCell = `<span style="font-size:0.75rem">${tp1Hit}${tp1} / ${tp2Hit}${tp2} / ${tp3}</span>`;
        const partialPnl = s.accumulated_pnl ? `<span style="color:var(--green);font-size:0.7rem">+${s.accumulated_pnl.toFixed(1)}%</span>` : "";
        const remaining = s.remaining_size ? `<span style="color:var(--text-muted);font-size:0.7rem">${(s.remaining_size*100).toFixed(0)}%</span>` : "";
        const statusCell = partialPnl || remaining ? `${partialPnl} ${remaining}` : "100%";
        return `<tr>
            <td><strong>${(s.symbol || "").replace("-USDT-SWAP","").replace("-USDT","")}</strong></td>
            <td>${dir}</td>
            <td class="mono">${fmtPrice(s.entry_price)}</td>
            <td class="mono td-sl">${fmtPrice(s.effective_sl || s.stop_loss)}</td>
            <td class="mono" style="font-size:0.78rem">${tpCell}</td>
            <td><span class="ame-rr-badge">${s.rr_ratio ? s.rr_ratio.toFixed(1) + "R" : "—"}</span></td>
            <td><span class="ame-score-badge">${s.score ? s.score.toFixed(0) : "—"}</span></td>
            <td><span class="ame-regime-tag ${regCls}">${s.regime || "—"}</span></td>
            <td>${statusCell}</td>
            <td class="td-muted">${elapsed}</td>
        </tr>`;
    }).join("");
}

function renderAMEHistoryTable(signals) {
    const tbody = document.getElementById("ameHistoryTable");
    if (!tbody) return;

    if (!signals || signals.length === 0) {
        tbody.innerHTML = `<tr><td colspan="9" class="ame-empty-state">
            <div class="ame-empty-icon" style="opacity:0.3"><i class="fas fa-clock-rotate-left"></i></div>
            <div class="ame-empty-desc">Henüz kapanmış AME işlemi yok</div>
        </td></tr>`;
        return;
    }

    tbody.innerHTML = signals.map(s => {
        const dir = s.direction === "LONG"
            ? '<span class="ame-dir long">▲ LONG</span>'
            : '<span class="ame-dir short">▼ SHORT</span>';
        const pnl = s.pnl_pct || 0;
        const pnlStr = (pnl >= 0 ? "+" : "") + pnl.toFixed(2) + "%";
        const pnlColor = pnl >= 0 ? "var(--green)" : "var(--red)";
        const statusBadge = s.status === "WON"
            ? '<span class="ame-result-badge won">WON</span>'
            : s.status === "LOST"
            ? '<span class="ame-result-badge lost">LOST</span>'
            : `<span class="ame-result-badge">${s.status}</span>`;
        const date = s.close_time ? new Date(s.close_time).toLocaleString("tr-TR", {day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"}) : "—";
        return `<tr>
            <td><strong>${(s.symbol || "").replace("-USDT-SWAP","").replace("-USDT","")}</strong></td>
            <td>${dir}</td>
            <td class="mono">${fmtPrice(s.entry_price)}</td>
            <td class="mono">${fmtPrice(s.close_price)}</td>
            <td style="color:${pnlColor};font-weight:700;font-family:var(--mono)">${pnlStr}</td>
            <td>${statusBadge}</td>
            <td>${s.score ? s.score.toFixed(0) : "—"}</td>
            <td>${s.mode || "—"}</td>
            <td class="td-date">${date}</td>
        </tr>`;
    }).join("");
}

function getElapsed(isoStr) {
    try {
        const diff = Date.now() - new Date(isoStr).getTime();
        const mins = Math.floor(diff / 60000);
        if (mins < 1) return "<1dk";
        if (mins < 60) return mins + "dk";
        const hours = Math.floor(mins / 60);
        return hours + "sa " + (mins % 60) + "dk";
    } catch { return "—"; }
}

function fmtPrice(p) {
    if (!p) return "—";
    if (p >= 100) return p.toFixed(2);
    if (p >= 1) return p.toFixed(4);
    return p.toFixed(6);
}

function setText(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
}

async function ameChangeMode(mode) {
    try {
        const res = await fetch("/api/ame/mode", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ mode })
        });
        if (res.ok) {
            showToast(`AME modu değiştirildi: ${mode}`, "info");
            loadAME();
        }
    } catch (e) {
        console.error("AME mode change error:", e);
    }
}

async function ameManualScan() {
    const btn = document.getElementById("ameScanBtn");
    if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Tarıyor...'; }
    const dot = document.getElementById("ameScanDot");
    if (dot) dot.classList.add("scanning");
    setText("ameScanText", "Tarama devam ediyor...");
    try {
        await fetch("/api/ame/scan", { method: "POST" });
        showToast("AME tarama başlatıldı", "info");
        // Poll for completion
        let checks = 0;
        const pollId = setInterval(async () => {
            checks++;
            if (checks > 30) { clearInterval(pollId); }
            try {
                const st = await apiFetch("/api/ame/status");
                if (st && st.last_scan && !st.last_scan.scanning) {
                    clearInterval(pollId);
                    loadAME();
                    if (btn) { btn.disabled = false; btn.innerHTML = '<i class="fas fa-satellite-dish"></i> Tara'; }
                }
            } catch {}
        }, 2000);
    } catch (e) {
        console.error("AME manual scan error:", e);
        if (btn) { btn.disabled = false; btn.innerHTML = '<i class="fas fa-satellite-dish"></i> Tara'; }
    }
}

// AME WebSocket events
if (typeof socket !== "undefined") {
    socket.on("ame_new_signal", (data) => {
        showToast(`AME Sinyal: ${data?.symbol || "?"} ${data?.direction || ""}`, "success");
        if (isActiveTab("ame")) loadAME();
    });
    socket.on("ame_trade_closed", (data) => {
        const won = data?.status === "WON";
        showToast(`AME ${data?.symbol || ""} ${won ? "KAZANDI" : "KAYBETTİ"} | PnL: ${data?.pnl_pct || 0}%`, won ? "success" : "error");
        if (isActiveTab("ame")) loadAME();
    });
    socket.on("ame_scan_complete", (data) => {
        if (isActiveTab("ame")) loadAME();
        const n = data?.new_signals || 0;
        if (n > 0) showToast(`AME tarama: ${n} yeni sinyal!`, "success");
    });
}

function isActiveTab(name) {
    const el = document.getElementById("tab-" + name);
    return el && el.classList.contains("active");
}
