const { createApp, ref, computed, onMounted } = Vue;

createApp({
  setup() {
    const bridge = window.AstrBotPluginPage;
    const tab = ref("users");
    const loading = ref(false);
    const errorMsg = ref("");
    const logs = ref([]);
    const users = ref([]);
    const groups = ref([]);
    const filterGroup = ref("");
    const filterUser = ref("");
    const filterLimit = ref(300);
    const bots = ref([]);
    const selectedBot = ref("");
    const selectedMode = ref("per_group");
    const scopeRevision = ref(0);
    const scopeLoading = ref(false);
    const modeLoading = ref(false);
    const backupLoading = ref(false);
    const restoreName = ref("");
    const latestBackup = ref("");
    const restorePreview = ref(null);

    const selectedBotInfo = computed(() =>
      bots.value.find((item) => item.bot_id === selectedBot.value) || null
    );
    const scopeLabel = computed(() =>
      selectedMode.value === "global" ? "全局空间" : "按群空间"
    );
    const scopeSnapshot = () => {
      if (!selectedBot.value || !selectedMode.value) return null;
      return { bot_id: selectedBot.value, scope_mode: selectedMode.value };
    };
    const scopedParams = (params = {}, snapshot = scopeSnapshot()) => {
      if (!snapshot) throw new Error("请先选择 Bot 和数据空间");
      return { ...snapshot, ...params };
    };
    const scopedGet = (endpoint, params = {}, snapshot = scopeSnapshot()) =>
      bridge.apiGet(endpoint, scopedParams(params, snapshot));
    const scopedPost = (endpoint, body = {}, snapshot = scopeSnapshot()) =>
      bridge.apiPost(endpoint, scopedParams(body, snapshot));
    const isCurrentScope = (snapshot, revision) =>
      revision === scopeRevision.value &&
      snapshot &&
      snapshot.bot_id === selectedBot.value &&
      snapshot.scope_mode === selectedMode.value;
    const clearScopeState = () => {
      scopeRevision.value += 1;
      loading.value = false;
      memberLoading.value = false;
      backupLoading.value = false;
      restoreName.value = "";
      logs.value = [];
      users.value = [];
      groups.value = [];
      filterGroup.value = "";
      selectedLog.value = null;
      memberDetail.value = null;
      undoPreview.value = null;
      restorePreview.value = null;
      latestBackup.value = "";
      errorMsg.value = "";
    };

    const pad = (n) => String(n).padStart(2, "0");
    const fmtTime = (ts) => {
      if (!ts) return "-";
      const d = new Date(ts * 1000);
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
    };
    const fmtNum = (v) => {
      const n = Number(v || 0);
      return Number.isInteger(n) ? String(n) : n.toFixed(1);
    };
    const fmtDelta = (v) => {
      const n = Number(v || 0);
      const s = Number.isInteger(n) ? String(n) : n.toFixed(1);
      return n > 0 ? `+${s}` : s;
    };
    const fmtIdle = (d) =>
      d == null ? "从未" : d <= 0 ? "今天" : d === 1 ? "昨天" : `${d}天前`;
    const fmtUser = (u) => {
      const id = u.user_id;
      const nick = (u.nickname || "").trim();
      if (!nick) return `(${id})`;
      let w = 0, out = "";
      for (const ch of nick) {
        const cw = ch.codePointAt(0) > 0x2e80 ? 2 : 1;
        if (w + cw > 16) { out += "…"; break; }
        out += ch; w += cw;
      }
      return `${out} (${id})`;
    };
    const SOURCE_LABEL = { rule: "规则", judge: "评估", admin: "管理员", tool: "工具", api: "API", set: "设定", undo: "撤销" };
    const sourceLabel = (s) => SOURCE_LABEL[s] || s || "-";

    // 七级好感色温轴：冷蓝紫(厌恶) → 灰 → 玫瑰 → 绯红(挚爱)，与排行图千咲配色同源
    const LEVEL_COLORS = {
      "厌恶": "#5563b5",
      "陌生": "#8f8a96",
      "认识": "#9e7fa8",
      "友好": "#b96b84",
      "亲密": "#cd5067",
      "挚友": "#ac2432",
      "挚爱": "#7f1220",
    };
    const levelColor = (level) => LEVEL_COLORS[level] || "#8f8a96";
    const levelChipStyle = (level) => {
      const c = levelColor(level);
      return { color: c, background: c + "14", borderColor: c + "38" };
    };
    // 好感度色温条：中线为原点，正值向右(暖)、负值向左(冷)，满刻度 ±100
    const barStyle = (u) => {
      const v = Number(u.favor || 0);
      const w = Math.min(Math.abs(v), 100) / 2;
      return {
        width: w + "%",
        background: levelColor(u.level),
        ...(v >= 0 ? { left: "50%" } : { right: "50%" }),
      };
    };

    const undoPreview = ref(null);
    const openUndo = async (r) => {
      const snapshot = scopeSnapshot();
      const revision = scopeRevision.value;
      if (!snapshot) return;
      errorMsg.value = "";
      try {
        const data = await scopedPost("undo", { id: r.id, dry: "1" }, snapshot);
        if (!isCurrentScope(snapshot, revision)) return;
        if (data && data.success === false) {
          errorMsg.value = data.error || "无法撤销";
          return;
        }
        undoPreview.value = { ...(data.preview || {}), id: r.id, snapshot, revision };
      } catch (e) {
        if (isCurrentScope(snapshot, revision)) errorMsg.value = String(e);
      }
    };
    const closeUndo = () => { undoPreview.value = null; };
    const confirmUndo = async () => {
      const preview = undoPreview.value;
      undoPreview.value = null;
      if (!preview || !isCurrentScope(preview.snapshot, preview.revision)) return;
      errorMsg.value = "";
      try {
        const data = await scopedPost("undo", { id: preview.id }, preview.snapshot);
        if (data && data.success === false) {
          errorMsg.value = data.error || "撤销失败";
        } else {
          await fetchLogs();
        }
      } catch (e) {
        errorMsg.value = String(e);
      }
    };

    const selectedLog = ref(null);
    const showDetail = (r) => { selectedLog.value = r; };
    const closeDetail = () => { selectedLog.value = null; };

    // 成员详情弹窗（印象/标签/近期评审）
    const memberDetail = ref(null);
    const memberLoading = ref(false);
    const openMember = async (u) => {
      const snapshot = scopeSnapshot();
      const revision = scopeRevision.value;
      if (!snapshot) return;
      const candidate = selectedMode.value === "global" ? filterGroup.value : u.group_id;
      const groupId = String(candidate || "");
      if (!/^[1-9][0-9]{0,31}$/.test(groupId)) {
        errorMsg.value = "全局空间请先选择真实群，再查看成员详情";
        return;
      }
      if (selectedMode.value === "global" && !groups.value.some((g) => g.group_id === groupId)) {
        errorMsg.value = "请选择当前空间中的真实群";
        return;
      }
      errorMsg.value = "";
      memberLoading.value = true;
      try {
        const data = await scopedGet("member", { group_id: groupId, user_id: u.user_id }, snapshot);
        if (!isCurrentScope(snapshot, revision)) return;
        if (data && data.success === false) {
          errorMsg.value = data.error || "加载成员详情失败";
        } else {
          memberDetail.value = data.member || null;
        }
      } catch (e) {
        if (isCurrentScope(snapshot, revision)) errorMsg.value = String(e);
      } finally {
        if (isCurrentScope(snapshot, revision)) memberLoading.value = false;
      }
    };
    const closeMember = () => { memberDetail.value = null; };
    const refreshImpression = async () => {
      const m = memberDetail.value;
      if (!m) return;
      const snapshot = scopeSnapshot();
      const revision = scopeRevision.value;
      if (!snapshot) return;
      errorMsg.value = "";
      memberLoading.value = true;
      try {
        const data = await scopedPost("refresh_impression", { group_id: m.group_id, user_id: m.user_id }, snapshot);
        if (!isCurrentScope(snapshot, revision)) return;
        if (data && data.success === false) {
          errorMsg.value = data.error || data.message || "刷新失败";
        } else {
          await openMember(m); // 重新拉取详情展示新印象
          await fetchUsers();
        }
      } catch (e) {
        if (isCurrentScope(snapshot, revision)) errorMsg.value = String(e);
      } finally {
        if (isCurrentScope(snapshot, revision)) memberLoading.value = false;
      }
    };

    const loadScope = async (snapshot = scopeSnapshot()) => {
      if (!snapshot) return;
      const revision = scopeRevision.value;
      scopeLoading.value = true;
      try {
        const [groupData, userData] = await Promise.all([
          scopedGet("groups", {}, snapshot),
          scopedGet("users", { limit: 1000 }, snapshot),
        ]);
        if (!isCurrentScope(snapshot, revision)) return;
        if (groupData && groupData.success === false) {
          throw new Error(groupData.error || "加载群列表失败");
        }
        if (userData && userData.success === false) {
          throw new Error(userData.error || "加载成员失败");
        }
        groups.value = (groupData && groupData.groups) || [];
        users.value = (userData && userData.users) || [];
        if (tab.value === "logs") await fetchLogs();
      } catch (e) {
        if (isCurrentScope(snapshot, revision)) {
          errorMsg.value = String(e);
          groups.value = [];
          users.value = [];
        }
      } finally {
        if (isCurrentScope(snapshot, revision)) scopeLoading.value = false;
      }
    };

    const fetchBots = async () => {
      scopeLoading.value = true;
      errorMsg.value = "";
      try {
        const data = await bridge.apiGet("bots");
        if (data && data.success === false) {
          throw new Error(data.error || "加载 Bot 列表失败");
        }
        bots.value = (data && data.bots) || [];
        const selected = bots.value.find((item) => item.bot_id === selectedBot.value) || bots.value[0];
        if (!selected) {
          selectedBot.value = "";
          selectedMode.value = "per_group";
          clearScopeState();
          return;
        }
        const changed = selectedBot.value !== selected.bot_id || selectedMode.value !== selected.scope_mode;
        selectedBot.value = selected.bot_id;
        selectedMode.value = selected.scope_mode || "per_group";
        if (changed) clearScopeState();
        await loadScope();
      } catch (e) {
        errorMsg.value = String(e);
        bots.value = [];
        selectedBot.value = "";
        clearScopeState();
      } finally {
        if (!selectedBot.value) scopeLoading.value = false;
      }
    };

    const selectBot = async () => {
      const item = selectedBotInfo.value;
      if (!item) return;
      selectedMode.value = item.scope_mode || "per_group";
      clearScopeState();
      await loadScope();
    };

    const applyMode = async () => {
      const botId = selectedBot.value;
      const nextMode = selectedMode.value;
      const item = bots.value.find((entry) => entry.bot_id === botId);
      const previousMode = (item && item.scope_mode) || "per_group";
      if (!botId || nextMode === previousMode) {
        await loadScope();
        return;
      }
      clearScopeState();
      modeLoading.value = true;
      try {
        const data = await bridge.apiPost("mode", { bot_id: botId, scope_mode: nextMode });
        if (data && data.success === false) {
          throw new Error(data.error || "切换数据空间失败");
        }
        if (item) item.scope_mode = nextMode;
        await loadScope();
      } catch (e) {
        selectedMode.value = previousMode;
        if (item) item.scope_mode = previousMode;
        clearScopeState();
        errorMsg.value = String(e);
        await loadScope();
      } finally {
        modeLoading.value = false;
      }
    };

    const backupScope = async () => {
      const snapshot = scopeSnapshot();
      const revision = scopeRevision.value;
      if (!snapshot) return;
      backupLoading.value = true;
      errorMsg.value = "";
      try {
        const data = await scopedPost("backup", {}, snapshot);
        if (!isCurrentScope(snapshot, revision)) return;
        if (data && data.success === false) {
          throw new Error(data.error || "备份失败");
        }
        latestBackup.value = data.backup_name || "";
        if (latestBackup.value) restoreName.value = latestBackup.value;
      } catch (e) {
        if (isCurrentScope(snapshot, revision)) errorMsg.value = String(e);
      } finally {
        if (isCurrentScope(snapshot, revision)) backupLoading.value = false;
      }
    };

    const prepareRestore = () => {
      const snapshot = scopeSnapshot();
      const name = restoreName.value.trim();
      if (!snapshot || !name) {
        errorMsg.value = "请输入备份文件名";
        return;
      }
      restorePreview.value = { backup_name: name, snapshot, revision: scopeRevision.value };
    };
    const closeRestore = () => { restorePreview.value = null; };
    const confirmRestore = async () => {
      const preview = restorePreview.value;
      restorePreview.value = null;
      if (!preview || !isCurrentScope(preview.snapshot, preview.revision)) return;
      backupLoading.value = true;
      errorMsg.value = "";
      try {
        const data = await scopedPost("restore", { backup_name: preview.backup_name }, preview.snapshot);
        if (!isCurrentScope(preview.snapshot, preview.revision)) return;
        if (data && data.success === false) {
          throw new Error(data.error || "恢复失败");
        }
        clearScopeState();
        await loadScope();
      } catch (e) {
        if (isCurrentScope(preview.snapshot, preview.revision)) errorMsg.value = String(e);
      } finally {
        if (isCurrentScope(preview.snapshot, preview.revision)) backupLoading.value = false;
      }
    };

    const fetchLogs = async () => {
      const snapshot = scopeSnapshot();
      const revision = scopeRevision.value;
      if (!snapshot) return;
      loading.value = true;
      errorMsg.value = "";
      try {
        const params = { limit: filterLimit.value };
        if (filterGroup.value) params.group_id = filterGroup.value;
        const u = filterUser.value.trim();
        // 搜索框保持子串匹配体验（后端默认精确匹配，仅此处显式开启模糊）
        if (u) { params.user_id = u; params.fuzzy = "1"; }
        const data = await scopedGet("logs", params, snapshot);
        if (!isCurrentScope(snapshot, revision)) return;
        if (data && data.success === false) {
          errorMsg.value = data.error || "加载失败";
          logs.value = [];
        } else {
          logs.value = (data && data.logs) || [];
        }
      } catch (e) {
        if (isCurrentScope(snapshot, revision)) {
          errorMsg.value = String(e);
          logs.value = [];
        }
      } finally {
        if (isCurrentScope(snapshot, revision)) loading.value = false;
      }
    };

    const fetchUsers = async () => {
      const snapshot = scopeSnapshot();
      const revision = scopeRevision.value;
      if (!snapshot) return;
      loading.value = true;
      errorMsg.value = "";
      try {
        const params = { limit: 1000 };
        if (filterGroup.value) params.group_id = filterGroup.value;
        const data = await scopedGet("users", params, snapshot);
        if (!isCurrentScope(snapshot, revision)) return;
        if (data && data.success === false) {
          errorMsg.value = data.error || "加载失败";
          users.value = [];
        } else {
          users.value = (data && data.users) || [];
        }
      } catch (e) {
        if (isCurrentScope(snapshot, revision)) {
          errorMsg.value = String(e);
          users.value = [];
        }
      } finally {
        if (isCurrentScope(snapshot, revision)) loading.value = false;
      }
    };

    const visibleUsers = computed(() => {
      const q = filterUser.value.trim();
      return q ? users.value.filter((u) => u.user_id.indexOf(q) !== -1) : users.value;
    });

    const refresh = () => (tab.value === "logs" ? fetchLogs() : fetchUsers());
    const switchTab = (t) => {
      tab.value = t;
      errorMsg.value = "";
      if (t === "logs" && !logs.value.length) fetchLogs();
      if (t === "users" && !users.value.length) fetchUsers();
    };

    onMounted(async () => {
      try {
        await bridge?.ready?.();
      } catch (e) {
        /* ignore */
      }
      await fetchBots();
    });

    return {
      tab, loading, errorMsg, logs, users, visibleUsers, groups,
      filterGroup, filterUser, filterLimit,
      bots, selectedBot, selectedMode, selectedBotInfo, scopeLabel,
      scopeLoading, modeLoading, backupLoading, restoreName, latestBackup,
      fmtTime, fmtNum, fmtDelta, fmtIdle, fmtUser, sourceLabel,
      levelColor, levelChipStyle, barStyle,
      undoPreview, openUndo, closeUndo, confirmUndo,
      selectedLog, showDetail, closeDetail,
      memberDetail, memberLoading, openMember, closeMember, refreshImpression,
      fetchBots, selectBot, applyMode, backupScope, prepareRestore,
      closeRestore, confirmRestore, fetchLogs, fetchUsers, refresh, switchTab,
      restorePreview,
    };
  },
  template: `
    <div class="xx-page">
      <header class="xx-header">
        <div class="xx-title">
          <h2>Bond 开发基线 · 关系面板</h2>
          <p class="xx-sub">兼容关系状态与变动记录 · 3D 关系模型待后续演进</p>
        </div>
        <nav class="xx-tabs">
          <button class="xx-tab" :class="{ active: tab === 'users' }" @click="switchTab('users')">当前总览</button>
          <button class="xx-tab" :class="{ active: tab === 'logs' }" @click="switchTab('logs')">变动流水</button>
        </nav>
      </header>
      <div class="xx-string"></div>

      <section class="xx-scope-bar">
        <div class="xx-scope-field">
          <label for="xx-bot-select">Bot</label>
          <select id="xx-bot-select" v-model="selectedBot" @change="selectBot" :disabled="scopeLoading || modeLoading">
            <option value="">选择 Bot</option>
            <option v-for="b in bots" :key="b.bot_id" :value="b.bot_id">{{ b.bot_id }}</option>
          </select>
        </div>
        <div class="xx-scope-field">
          <label for="xx-mode-select">数据空间</label>
          <select id="xx-mode-select" v-model="selectedMode" @change="applyMode" :disabled="!selectedBot || scopeLoading || modeLoading">
            <option value="per_group">按群空间</option>
            <option value="global">全局空间</option>
          </select>
        </div>
        <div class="xx-scope-current">
          <span>当前模式</span>
          <strong>{{ selectedBot ? scopeLabel : "未选择" }}</strong>
        </div>
        <div class="xx-scope-actions">
          <button class="xx-btn xx-btn-mini" @click="backupScope" :disabled="!selectedBot || backupLoading || scopeLoading">
            {{ backupLoading ? "处理中…" : "备份当前空间" }}
          </button>
          <input class="xx-input xx-backup-name" v-model="restoreName" placeholder="备份文件名">
          <button class="xx-btn xx-btn-danger xx-btn-mini" @click="prepareRestore" :disabled="!selectedBot || backupLoading || !restoreName.trim()">
            恢复
          </button>
        </div>
        <span v-if="latestBackup" class="xx-scope-note">最近备份：<code>{{ latestBackup }}</code></span>
      </section>

      <div class="xx-filters">
        <select v-model="filterGroup" @change="refresh">
          <option value="">全部群</option>
          <option v-for="g in groups" :key="g.group_id" :value="g.group_id">
            {{ g.group_id }}（{{ g.count }}）
          </option>
        </select>
        <input class="xx-input" v-model="filterUser" placeholder="按 QQ 号筛选（模糊）" @keyup.enter="tab === 'logs' && fetchLogs()">
        <select v-if="tab === 'logs'" v-model="filterLimit" @change="fetchLogs">
          <option :value="100">最近 100</option>
          <option :value="300">最近 300</option>
          <option :value="1000">最近 1000</option>
        </select>
        <button class="xx-btn" @click="refresh" :disabled="loading">
          {{ loading ? "加载中…" : "刷新" }}
        </button>
      </div>

      <p v-if="errorMsg" class="xx-error">{{ errorMsg }}</p>

      <!-- 当前总览 -->
      <div v-if="tab === 'users'" class="xx-table-wrap">
        <table class="xx-table">
          <thead>
            <tr>
              <th>群</th><th>QQ</th><th>好感(有效)</th><th>等级</th><th>关系</th><th>最近互动</th><th>状态</th><th>印象/标签</th><th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!visibleUsers.length">
              <td colspan="9" class="xx-empty">{{ loading ? "加载中…" : (users.length ? "无匹配成员" : "暂无成员") }}</td>
            </tr>
            <tr v-for="u in visibleUsers" :key="u.group_id + '_' + u.user_id">
              <td class="xx-mono">{{ u.group_id }}</td>
              <td class="xx-mono">{{ fmtUser(u) }}</td>
              <td>
                <div class="xx-favor">
                  <div class="xx-favor-top">
                    <span class="xx-favor-num" :style="{ color: levelColor(u.level) }">{{ fmtNum(u.favor) }}</span>
                    <span v-if="u.decayed" class="xx-tag" :title="'原 ' + fmtNum(u.stored_favor)">衰减</span>
                  </div>
                  <div class="xx-favor-bar"><i :style="barStyle(u)"></i></div>
                </div>
              </td>
              <td><span class="xx-level-chip" :style="levelChipStyle(u.level)"><i></i>{{ u.level }}</span></td>
              <td>{{ u.relationship || "-" }}</td>
              <td class="xx-mono">{{ fmtIdle(u.idle_days) }}</td>
              <td>
                <span v-if="u.decayed" class="xx-tag">衰减中</span>
                <span v-else-if="u.idle_days != null && u.idle_days >= 3" class="xx-tag">{{ u.idle_days }}天未动</span>
                <span v-else>-</span>
              </td>
              <td class="xx-imp-cell">
                <div class="xx-imp">
                  <div v-if="u.tags && u.tags.length" class="xx-chips">
                    <span v-for="t in u.tags" :key="t" class="xx-chip">{{ t }}</span>
                  </div>
                  <p v-if="u.impression" class="xx-imp-text" :title="u.impression">{{ u.impression.length > 12 ? u.impression.slice(0, 12) + '…' : u.impression }}</p>
                  <span v-if="!u.impression && !(u.tags && u.tags.length)" class="xx-muted">-</span>
                </div>
              </td>
              <td class="xx-actions">
                <button class="xx-btn xx-btn-mini" @click="openMember(u)" :disabled="selectedMode === 'global' && !filterGroup" :title="selectedMode === 'global' && !filterGroup ? '请先选择真实群' : '查看成员详情'">详情</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 变动流水 -->
      <div v-if="tab === 'logs'" class="xx-table-wrap">
        <table class="xx-table">
          <thead>
            <tr>
              <th>时间</th><th>群</th><th>QQ</th><th>增减</th><th>变化</th><th>原因</th><th>来源</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!logs.length">
              <td colspan="8" class="xx-empty">{{ loading ? "加载中…" : "暂无记录" }}</td>
            </tr>
            <tr v-for="r in logs" :key="r.id" :class="{ 'xx-reversed': r.reversed }">
              <td class="xx-mono">{{ fmtTime(r.ts) }}</td>
              <td class="xx-mono">{{ r.group_id }}</td>
              <td class="xx-mono">{{ r.user_id }}</td>
              <td :class="r.delta > 0 ? 'xx-up' : (r.delta < 0 ? 'xx-down' : 'xx-zero')">{{ fmtDelta(r.delta) }}</td>
              <td class="xx-mono">{{ fmtNum(r.favor_before) }} → {{ fmtNum(r.favor_after) }}</td>
              <td>{{ r.reason || "-" }}</td>
              <td><span class="xx-tag">{{ sourceLabel(r.source) }}</span></td>
              <td class="xx-actions">
                <button class="xx-btn xx-btn-mini" @click="showDetail(r)">详情</button>
                <span v-if="r.reversed" class="xx-tag xx-muted">已撤销</span>
                <button v-else class="xx-btn xx-btn-undo" @click="openUndo(r)" :disabled="loading">撤销</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 变动详情弹窗：完整发言 + AI 原因 -->
      <div v-if="selectedLog" class="xx-modal" @click.self="closeDetail">
        <div class="xx-modal-box">
          <header class="xx-modal-header">
            <span>变动详情</span>
            <button class="xx-modal-close" @click="closeDetail">×</button>
          </header>
          <div class="xx-modal-meta xx-mono">
            {{ fmtTime(selectedLog.ts) }} · 群 {{ selectedLog.group_id }} · QQ {{ selectedLog.user_id }}
            · <span :class="selectedLog.delta > 0 ? 'xx-up' : (selectedLog.delta < 0 ? 'xx-down' : 'xx-zero')">{{ fmtDelta(selectedLog.delta) }}</span>
            · {{ fmtNum(selectedLog.favor_before) }} → {{ fmtNum(selectedLog.favor_after) }}
            · <span class="xx-tag">{{ sourceLabel(selectedLog.source) }}</span>
          </div>
          <section class="xx-modal-section">
            <h4>用户发言</h4>
            <p class="xx-modal-text">{{ selectedLog.message || "（本次变动非评估触发，无发言记录）" }}</p>
          </section>
          <section class="xx-modal-section">
            <h4>AI 原因</h4>
            <p class="xx-modal-text">{{ selectedLog.reason || "-" }}</p>
          </section>
        </div>
      </div>

      <!-- 成员详情弹窗：印象 + 标签 + 近期评审 -->
      <div v-if="memberDetail" class="xx-modal" @click.self="closeMember">
        <div class="xx-modal-box">
          <header class="xx-modal-header">
            <span>成员详情</span>
            <button class="xx-modal-close" @click="closeMember">×</button>
          </header>
          <div class="xx-modal-meta xx-mono">
            QQ {{ memberDetail.user_id }}
            <span v-if="memberDetail.nickname">（{{ memberDetail.nickname }}）</span>
            · 群 {{ memberDetail.group_id }}
            <span v-if="memberDetail.is_master" class="xx-tag">主人</span>
          </div>
          <div class="xx-member-stats">
            <div class="xx-member-stat">
              <label>好感</label>
              <b :style="{ color: levelColor(memberDetail.level) }">{{ fmtNum(memberDetail.favor) }}</b>
            </div>
            <div class="xx-member-stat">
              <label>等级</label>
              <span class="xx-level-chip" :style="levelChipStyle(memberDetail.level)"><i></i>{{ memberDetail.level }}</span>
            </div>
            <div class="xx-member-stat">
              <label>关系</label>
              <span>{{ memberDetail.relationship || "-" }}</span>
            </div>
          </div>
          <section class="xx-modal-section">
            <h4>印象</h4>
            <div v-if="memberDetail.tags && memberDetail.tags.length" class="xx-chips">
              <span v-for="t in memberDetail.tags" :key="t" class="xx-chip">{{ t }}</span>
            </div>
            <p class="xx-modal-text">{{ memberDetail.impression || "（尚未生成，点下方按钮立即刷新）" }}</p>
          </section>
          <section class="xx-modal-section">
            <h4>近期评审</h4>
            <p v-if="!memberDetail.recent_judges || !memberDetail.recent_judges.length" class="xx-modal-text xx-muted-text">暂无评估记录</p>
            <ul v-else class="xx-judge-list">
              <li v-for="(j, i) in memberDetail.recent_judges" :key="i">
                <span class="xx-mono">{{ fmtTime(j.ts) }}</span>
                <span :class="j.delta > 0 ? 'xx-up' : (j.delta < 0 ? 'xx-down' : 'xx-zero')">{{ fmtDelta(j.delta) }}</span>
                <span class="xx-judge-reason">{{ j.reason || "-" }}</span>
              </li>
            </ul>
          </section>
          <div class="xx-modal-actions">
            <button class="xx-btn xx-btn-mini" @click="closeMember">关闭</button>
            <button class="xx-btn" @click="refreshImpression" :disabled="memberLoading">
              {{ memberLoading ? "处理中…" : "立即刷新印象" }}
            </button>
          </div>
        </div>
      </div>

      <!-- 撤销确认弹窗：预览撤销后好感 -->
      <div v-if="undoPreview" class="xx-modal" @click.self="closeUndo">
        <div class="xx-modal-box">
          <header class="xx-modal-header">
            <span>撤销这条变动？</span>
            <button class="xx-modal-close" @click="closeUndo">×</button>
          </header>
          <section class="xx-modal-section">
            <p class="xx-modal-text">
              撤销后好感度：<b class="xx-up" style="font-size:16px">{{ fmtNum(undoPreview.after) }}</b>
              <span class="xx-muted-text">（当前 {{ fmtNum(undoPreview.current) }}，本次 {{ fmtDelta(undoPreview.delta) }}）</span>
            </p>
            <p class="xx-modal-text xx-muted-text">原因：{{ undoPreview.reason || "-" }}</p>
          </section>
          <div class="xx-modal-actions">
            <button class="xx-btn xx-btn-mini" @click="closeUndo">取消</button>
            <button class="xx-btn xx-btn-danger" @click="confirmUndo">确认撤销</button>
          </div>
        </div>
      </div>

      <!-- 恢复确认弹窗：恢复前要求确认当前 Bot/空间与备份名 -->
      <div v-if="restorePreview" class="xx-modal" @click.self="closeRestore">
        <div class="xx-modal-box">
          <header class="xx-modal-header">
            <span>确认恢复数据空间？</span>
            <button class="xx-modal-close" @click="closeRestore">×</button>
          </header>
          <section class="xx-modal-section">
            <p class="xx-modal-text">
              将恢复 Bot <b class="xx-mono">{{ restorePreview.snapshot.bot_id }}</b> 的
              <b>{{ restorePreview.snapshot.scope_mode === 'global' ? '全局空间' : '按群空间' }}</b>。
            </p>
            <p class="xx-modal-text xx-muted-text">备份：{{ restorePreview.backup_name }}</p>
            <p class="xx-modal-text xx-muted-text">当前空间会被备份后替换，确认后再读取。</p>
          </section>
          <div class="xx-modal-actions">
            <button class="xx-btn xx-btn-mini" @click="closeRestore">取消</button>
            <button class="xx-btn xx-btn-danger" @click="confirmRestore">确认恢复</button>
          </div>
        </div>
      </div>
    </div>
  `,
}).mount("#app");
