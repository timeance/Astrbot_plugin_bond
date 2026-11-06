const { createApp, ref, computed, onMounted, onUnmounted } = Vue;

createApp({
  setup() {
    const bridge = window.AstrBotPluginPage;
    const theme = ref(document.documentElement.dataset.theme || document.documentElement.dataset.bondTheme || "light");
    let manualTheme = false;
    const applyHostTheme = (context) => {
      if (manualTheme || typeof context?.isDark !== "boolean") return;
      theme.value = context.isDark ? "dark" : "light";
      document.documentElement.dataset.bondTheme = theme.value;
    };
    applyHostTheme(bridge?.getContext?.());
    const unsubscribeTheme = bridge?.onContext?.(applyHostTheme);
    onUnmounted(() => unsubscribeTheme?.());
    const toggleTheme = () => {
      manualTheme = true;
      theme.value = theme.value === "dark" ? "light" : "dark";
      document.documentElement.dataset.bondTheme = theme.value;
    };
    const tab = ref("users");
    const loading = ref(false);
    const errorMsg = ref("");
    const logs = ref([]);
    const statusLabels = {
      small: "初评", large: "复核", ok: "成功", failed: "失败", timeout: "超时",
      cancelled: "已取消", invalid: "结果无效", accepted: "已更新关系", unchanged: "关系未变化",
      abstained: "未作判断", disabled: "未启用", filtered: "已跳过", temporary: "临时关系中",
      busy: "忙碌，已跳过", duplicate_or_cooldown: "重复或间隔过短", pending: "处理中",
      reserved: "等待结果", budget: "次数已用完", builtin: "内置默认", global: "全局设置", group: "本群设置",
    };
    const displayStatus = status => Object.prototype.hasOwnProperty.call(statusLabels, status) ? statusLabels[status] : status;
    const reasonLabels = {
      normal_question: "日常问答", friendly: "表达友好", thanks: "表达感谢",
      joke: "友善玩笑", criticism: "提出批评", insult: "辱骂冒犯",
      manipulation: "操纵诱导", apology: "表达歉意", uncertain: "无法确定",
    };
    const displayReason = reason => {
      const text = String(reason || "");
      return Object.prototype.hasOwnProperty.call(reasonLabels, text) ? reasonLabels[text] : text || "-";
    };
    const users = ref([]);
    const todaySummary = ref(null);
    const todayLoading = ref(false);
    const todayError = ref("");
    let todayRequestId = 0;
    const userPage = ref(1);
    const userPageSize = ref(50);
    const userPages = ref(1);
    const userTotal = ref(0);
    const userMatchedCount = ref(0);
    const userCollapsedCount = ref(0);
    const userZeroCount = ref(0);
    const includeZeroUsers = ref(false);
    const showUserImpressions = ref(false);
    const toggleUserImpressions = () => { showUserImpressions.value = !showUserImpressions.value; };
    let userRequestId = 0;
    const groups = ref([]);
    const filterGroup = ref("");
    const filterUser = ref("");
    const filterLimit = ref(300);
    const logDimension = ref("all");
    const logSource = ref("");
    const logDimensions = [
      {key: "familiarity", label: "熟悉度"},
      {key: "affinity", label: "亲近度"},
      {key: "trust", label: "信任度"},
    ];
    let logRequestId = 0;
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
    const memberNotice = ref("");
    const memberNoticeError = ref(false);
    const memberSaveLoading = ref(false);
    const relationEditMode = ref("permanent");
    const temporaryMinutes = ref(60);
    const relationForm = ref({ familiarity: 0, affinity: 0, trust: 0, reason: "管理员调整" });
    const profileForm = ref({ impression: "", tagsText: "" });
    const promptKind = ref("behavior");
    const promptLevel = ref("global");
    const promptGroupId = ref("");
    const promptLoading = ref(false);
    const promptSaving = ref(false);
    const promptSettings = ref(null);
    const promptMode = ref("default");
    const promptTemplate = ref("");
    const promptResolved = ref("");
    const promptPreview = ref("");
    const promptNotice = ref("");
    const promptNoticeError = ref(false);
    const promptRequestTarget = ref(null);
    const memberRequestTarget = ref(null);
    let memberSession = 0;
    let lastTriggerEl = null;
    const rememberTrigger = () => {
      const active = typeof document !== "undefined" ? document.activeElement : null;
      if (active && typeof active.focus === "function") lastTriggerEl = active;
    };
    const restoreTriggerFocus = () => {
      if (lastTriggerEl && typeof lastTriggerEl.focus === "function") {
        try { lastTriggerEl.focus(); } catch { /* 忽略不可聚焦节点 */ }
      }
      lastTriggerEl = null;
    };
    const evaluationLoading = ref(false);
    const modelSaving = ref(false);
    const modelConfig = ref({});
    const modelRevision = ref("");
    let evaluationRequestId = 0;
    const activeModelConfig = ref({});
    const maxConcurrentAssessments = ref(2);
    const activeMaxConcurrentAssessments = ref(null);
    const assessmentStats = ref({ calls: [], events: {}, currency_cost: null });
    const modelNotice = ref("");
    const modelNoticeError = ref(false);
    const diagnosticStats = ref(null);
    const diagnosticLoading = ref(false);
    const diagnosticNotice = ref("");
    const diagnosticDownload = ref("");
    const diagnosticConfirm = ref(false);
    const releaseDiagnosticDownload = () => {
      if (diagnosticDownload.value) URL.revokeObjectURL(diagnosticDownload.value);
      diagnosticDownload.value = "";
    };
    const diagnosticRequest = async (action = "status") => {
      const snapshot = scopeSnapshot(), revision = scopeRevision.value;
      if (!snapshot || diagnosticLoading.value) return;
      if (action === "clear" && !diagnosticConfirm.value) { diagnosticConfirm.value = true; return; }
      diagnosticLoading.value = true; diagnosticNotice.value = ""; diagnosticConfirm.value = false;
      try {
        const data = action === "clear"
          ? await scopedPost("diagnostics", {action}, snapshot)
          : await scopedGet("diagnostics", {action}, snapshot);
        if (!isCurrentScope(snapshot, revision)) return;
        if (!data.success) throw new Error(data.error || "诊断操作失败");
        releaseDiagnosticDownload();
        if (action === "export") {
          diagnosticDownload.value = URL.createObjectURL(new Blob(
            [JSON.stringify(data.diagnostics, null, 2)], {type:"application/json"}));
          diagnosticNotice.value = "诊断记录已准备好，可以下载查看。";
        } else {
          diagnosticStats.value = data.diagnostics;
          diagnosticNotice.value = action === "clear" ? "当前 Bot 和数据空间的诊断记录已清除，关系数据保留。" : "";
        }
      } catch (error) {
        if (isCurrentScope(snapshot, revision)) diagnosticNotice.value = String(error);
      } finally {
        if (isCurrentScope(snapshot, revision)) diagnosticLoading.value = false;
      }
    };
    onUnmounted(releaseDiagnosticDownload);
    const detailSettings = ref(null);
    const detailValues = ref({});
    const detailLoading = ref(false);
    const detailNotice = ref("");
    const detailNoticeError = ref(false);
    let settingsRequestId = 0;
    let settingsSaving = false;
    const detailGroups = computed(() => detailSettings.value ? [...new Set(detailSettings.value.fields.map(f => f.group))].map(group => ({group, title: detailSettings.value.fields.find(f => f.group === group).title, fields: detailSettings.value.fields.filter(f => f.group === group)})) : []);
    const settingsRequest = async (save = false) => {
      if (settingsSaving || (save && (detailLoading.value || !detailSettings.value))) return;
      const requestId = ++settingsRequestId;
      settingsSaving = save;
      detailLoading.value = true;
      detailNotice.value = "";
      detailNoticeError.value = false;
      try {
        if (save && detailValues.value["assessment.enabled"] && !String(detailValues.value["assessment.small_provider_id"] || "").trim()) {
          throw new Error("启用自动评估前，请选择日常评估模型。");
        }
        const values = Object.fromEntries(Object.entries(detailValues.value).filter(([key,value]) => JSON.stringify(value) !== JSON.stringify(detailSettings.value?.values[key])));
        if (save && !Object.keys(values).length) { detailNotice.value = "没有需要保存的修改"; return; }
        const data = save ? await bridge.apiPost("settings", {values, revision: detailSettings.value.revision}) : await bridge.apiGet("settings", {});
        if (requestId !== settingsRequestId) return;
        if (!data.success) throw new Error(data.error || "设置读取失败，请重试");
        detailSettings.value = data;
        detailValues.value = {...data.values};
        if (save) detailNotice.value = "设置已保存，重载插件后生效。";
      } catch(error) {
        if (requestId === settingsRequestId) {
          detailNotice.value = String(error);
          detailNoticeError.value = true;
        }
      } finally {
        if (requestId === settingsRequestId) {
          detailLoading.value = false;
          settingsSaving = false;
        }
      }
    };
    const updateListSetting = (path, raw) => { detailValues.value[path] = raw.split(/[，,\s]+/).filter(Boolean); };
    const listPlaceholder = (path) => ({
      group_whitelist: "留空对所有群生效，多个群号用逗号分隔",
      ignored_bot_ids: "留空不忽略，多个机器人账号用逗号分隔",
      master_ids: "可选，多个主人账号用逗号分隔",
    })[path] || "多个项目用逗号分隔";
    const providerOptions = (path) => {
      const options = (detailSettings.value?.providers || []).map(({id, model}) => ({id, label: model ? `${model} · ${id}` : id}));
      const selected = detailValues.value[path];
      if (selected && !options.some(option => option.id === selected)) options.push({id: selected, label: `${selected}（暂不可用）`});
      return options;
    };
    const loadSettings = () => settingsRequest();
    const saveSettings = () => settingsRequest(true);
    const restoreSectionDefaults = (group) => {
      if (detailLoading.value || !detailSettings.value || group === "basic") return;
      for (const field of detailSettings.value.fields) {
        if (field.group === group && !field.path.endsWith("provider_id")) {
          detailValues.value[field.path] = Array.isArray(field.default) ? [...field.default] : field.default;
        }
      }
      detailNotice.value = "已填入默认设置。保存并重载后生效，刷新可撤回；模型选择保持不变。";
      detailNoticeError.value = false;
    };
    const modeMultiplier = values => values.relation_mode === "galgame" ? 2 : 1;
    const activeRuleSummary = computed(() => {
      const values = detailSettings.value?.active_values;
      if (!values) return null;
      const multiplier = modeMultiplier(values);
      return {
        mode: values.relation_mode === "galgame" ? "Galgame ×2" : "基础 ×1",
        caps: ["familiarity", "affinity", "trust"].map(key => ({key,
          label: {familiarity:"熟悉度", affinity:"亲近度", trust:"信任度"}[key],
          up: values[`relation_daily_caps.${key}_up`] * multiplier,
          down: values[`relation_daily_caps.${key}_down`],
        })),
        baselines: {
          familiarity: values["decay.familiarity_baseline"],
          affinity: values["decay.affinity_baseline"],
          trust: values["decay.trust_baseline"],
        },
        familiarity: values["decay.familiarity_enabled"] ? `${values["decay.familiarity_half_life"]} 天半衰期` : "关闭",
        affinity: values["decay.enabled"] ? `${values["decay.half_life_base"]} 天基础半衰期` : "关闭",
        trust: values["decay.trust_enabled"] ? `${values["decay.trust_half_life"]} 天半衰期` : "关闭",
      };
    });

    const DEFAULT_MODEL_CONFIG = {
      enabled: false,
      small_provider_id: "",
      large_provider_id: "",
      assessment_mode: "tiered",
      small_timeout_sec: 20,
      large_timeout_sec: 30,
      window_sec: 300,
      window_max_calls: 5,
      per_user_daily_small_cap: 30,
      per_user_daily_large_cap: 8,
      per_bot_daily_small_cap: 300,
      per_bot_daily_large_cap: 100,
      context_turns: 6,
      context_chars: 4000,
      context_days: 7,
      context_events: 8,
    };
    const evaluationFields = [
      {key:"small_timeout_sec", label:"初评超时（秒）", hint:"等待日常模型的最长时间。超时后本次不评分，已发起的调用仍计入用量。", min:0.1, step:0.1},
      {key:"large_timeout_sec", label:"复核超时（秒）", hint:"等待复核模型的最长时间。必须复核的互动若超时，本次不评分。", min:0.1, step:0.1},
      {key:"window_sec", label:"初评统计时段（秒）", hint:"向前回看这段时间，限制同一成员的初评次数。同一 Bot 下跨群合计。", min:1, step:1},
      {key:"window_max_calls", label:"时段内初评次数", hint:"每名成员在窗口内最多初评多少次。不同互动可分别评分；0 表示暂停初评。", min:0, step:1},
      {key:"per_user_daily_small_cap", label:"每人每日初评上限", hint:"同一 Bot 每天为每名成员初评的次数，跨群合计。UTC 00:00 重置；0 表示暂停初评。", min:0, step:1},
      {key:"per_user_daily_large_cap", label:"每人每日复核上限", hint:"每名成员每天最多复核多少次。复核来自已初评的互动；0 表示不复核。UTC 00:00 重置。", min:0, step:1},
      {key:"per_bot_daily_small_cap", label:"每 Bot 每日初评上限", hint:"这个 Bot 每天的初评总次数，跨群和数据空间合计。UTC 00:00 重置；0 表示暂停初评。", min:0, step:1},
      {key:"per_bot_daily_large_cap", label:"每 Bot 每日复核上限", hint:"这个 Bot 每天的复核总次数，跨群和数据空间合计。UTC 00:00 重置；0 表示不复核。", min:0, step:1},
      {key:"context_turns", label:"近期发言条数", hint:"给模型参考的近期发言与 Bot 回复条数，仅限同群、同成员。0 表示不带近期发言。", min:0, max:20, step:1},
      {key:"context_chars", label:"参考内容字数上限", hint:"限制引用、近期发言和已计分摘要的总长度。0 表示不带这些内容，当前消息仍会送评。", min:0, max:20000, step:1},
      {key:"context_days", label:"历史回顾天数", hint:"只参考这些天内的发言和已计分摘要，帮助模型理解上下文。0 表示不回顾历史。", min:0, max:90, step:1},
      {key:"context_events", label:"已评估互动条数", hint:"最多参考多少条本群已计分事件，帮助识别重复表达。历史不再计分；0 表示不带摘要。", min:0, max:20, step:1},
    ];
    const restoreEvaluationDefaults = () => {
      if (evaluationLoading.value || modelSaving.value) return;
      for (const field of evaluationFields) modelConfig.value[field.key] = DEFAULT_MODEL_CONFIG[field.key];
      maxConcurrentAssessments.value = 2;
      setModelNotice("已填入评估参数默认值；模型选择、评估模式和开关保持原值。保存并重载后生效。");
    };

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
      clearExpiryTimer();
      releaseDiagnosticDownload(); diagnosticStats.value = null; diagnosticLoading.value = false;
      diagnosticNotice.value = ""; diagnosticConfirm.value = false;
      scopeRevision.value += 1;
      todayRequestId += 1;
      todaySummary.value = null;
      todayLoading.value = false;
      todayError.value = "";
      loading.value = false;
      memberLoading.value = false;
      memberSaveLoading.value = false;
      backupLoading.value = false;
      restoreName.value = "";
      logs.value = [];
      logRequestId += 1;
      users.value = [];
      userRequestId += 1;
      userPage.value = 1;
      userPages.value = 1;
      userTotal.value = 0;
      userMatchedCount.value = 0;
      userCollapsedCount.value = 0;
      userZeroCount.value = 0;
      includeZeroUsers.value = false;
      filterUser.value = "";
      groups.value = [];
      filterGroup.value = "";
      selectedLog.value = null;
      memberDetail.value = null;
      undoPreview.value = null;
      restorePreview.value = null;
      latestBackup.value = "";
      errorMsg.value = "";
      memberNotice.value = "";
      memberNoticeError.value = false;
      relationForm.value = { familiarity: 0, affinity: 0, trust: 0, reason: "管理员调整" };
      profileForm.value = { impression: "", tagsText: "" };
      promptLoading.value = false;
      promptSaving.value = false;
      promptSettings.value = null;
      promptResolved.value = "";
      promptPreview.value = "";
      promptNotice.value = "";
      promptNoticeError.value = false;
      promptRequestTarget.value = null;
      memberRequestTarget.value = null;
      evaluationLoading.value = false;
      evaluationRequestId += 1;
      modelSaving.value = false;
      modelRevision.value = "";
      modelConfig.value = {};
      activeModelConfig.value = {};
      activeMaxConcurrentAssessments.value = null;
      assessmentStats.value = { calls: [], events: {}, currency_cost: null };
      modelNotice.value = "";
      modelNoticeError.value = false;
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
    const fmtOptionalNum = (v) => (v == null || v === "" ? "-" : fmtNum(v));
    const fmtSignedNum = (v) => {
      if (v == null || v === "") return "-";
      const n = Number(v);
      if (!Number.isFinite(n)) return "-";
      const s = Number.isInteger(n) ? String(n) : n.toFixed(1);
      return n > 0 ? `+${s}` : s;
    };
    const dimBarStyle = (dim, val) => {
      const n = Number(val);
      if (val == null || val === "" || !Number.isFinite(n)) {
        return { left: dim === "familiarity" ? "0%" : "50%", width: "0%" };
      }
      if (dim === "familiarity") {
        const pct = Math.max(0, Math.min(100, n));
        return { left: "0%", width: `${pct}%` };
      }
      const clamped = Math.max(-100, Math.min(100, n));
      const width = Math.abs(clamped) / 2;
      return {
        left: clamped >= 0 ? "50%" : `${50 - width}%`,
        width: `${width}%`,
      };
    };
    const logDeltaParts = (row, dimension = logDimension.value) => logDimensions
      .filter(item => dimension === "all" || item.key === dimension)
      .map(item => {
        const vector = row.relation_delta;
        const raw = vector && Object.prototype.hasOwnProperty.call(vector, item.key)
          ? vector[item.key] : item.key === "affinity" ? row.delta : null;
        return {...item, value: typeof raw === "number" && Number.isFinite(raw) ? raw : null};
      });
    const fmtKnownDelta = (value) => value == null ? "未知" : fmtDelta(value);
    const fmtDelta = (v) => {
      const n = Number(v || 0);
      const s = Number.isInteger(n) ? String(n) : n.toFixed(1);
      return n > 0 ? `+${s}` : s;
    };
    const fmtIdle = (d) =>
      d == null ? "从未" : d <= 0 ? "今天" : d === 1 ? "昨天" : `${d}天前`;
    const isRealAccount = (value) => /^[1-9][0-9]{0,31}$/.test(String(value || ""));
    const setMemberNotice = (message, failed = false) => {
      memberNotice.value = message;
      memberNoticeError.value = failed;
    };
    const setPromptNotice = (message, failed = false) => {
      promptNotice.value = message;
      promptNoticeError.value = failed;
    };
    const setModelNotice = (message, failed = false) => {
      modelNotice.value = message;
      modelNoticeError.value = failed;
    };
    const sameRequestTarget = (left, right, keys) => Boolean(left && right) &&
      keys.every((key) => String(left[key] ?? "") === String(right[key] ?? ""));
    const currentPromptFields = () => ({
      kind: promptKind.value,
      level: promptLevel.value,
      group_id: promptLevel.value === "group" ? String(promptGroupId.value || "") : "",
    });
    const isCurrentPromptRequest = (target, revision) =>
      isCurrentScope(target, revision) &&
      sameRequestTarget(target, promptRequestTarget.value, ["kind", "level", "group_id"]) &&
      sameRequestTarget(target, currentPromptFields(), ["kind", "level", "group_id"]);
    const isCurrentMemberRequest = (target, revision) =>
      isCurrentScope(target, revision) &&
      sameRequestTarget(target, memberRequestTarget.value, ["group_id", "user_id", "session"]);
    const normalizeModelConfig = (config = {}) => ({
      ...DEFAULT_MODEL_CONFIG,
      ...(config && typeof config === "object" ? config : {}),
    });
    const promptGroupIsValid = () => isRealAccount(promptGroupId.value);
    const promptRequest = () => {
      const params = { kind: promptKind.value, level: promptLevel.value };
      if (promptLevel.value === "group") {
        if (!promptGroupIsValid()) throw new Error("指定群请选择真实数字群");
        params.group_id = String(promptGroupId.value);
      }
      return params;
    };
    const promptRecord = (settings) => {
      if (!settings || typeof settings !== "object") return { mode: "default", template: "" };
      if (promptLevel.value === "global") {
        return settings.global || { mode: "default", template: "" };
      }
      const botGroups = settings.groups && settings.groups[selectedBot.value];
      const groupSettings = botGroups && botGroups[String(promptGroupId.value)];
      return (groupSettings && groupSettings[promptKind.value]) || { mode: "default", template: "" };
    };
    const syncPromptForm = (settings, resolved = "") => {
      const record = promptRecord(settings);
      promptSettings.value = settings || null;
      promptMode.value = record.mode === "custom" ? "custom" : "default";
      promptTemplate.value = record.mode === "custom" ? String(record.template || "") : "";
      promptResolved.value = typeof resolved === "string" ? resolved : "";
      promptPreview.value = "";
    };
    const parseTags = (value) => Array.from(new Set(
      String(value || "")
        .split(/[，,\n]+/)
        .map((tag) => tag.trim())
        .filter(Boolean)
    ));
    const fmtUser = (u) => {
      const id = u.user_id;
      const nick = (u.nickname || "").trim();
      if (!nick) return `昵称未知（${id}）`;
      let w = 0, out = "";
      for (const ch of nick) {
        const cw = ch.codePointAt(0) > 0x2e80 ? 2 : 1;
        if (w + cw > 16) { out += "…"; break; }
        out += ch; w += cw;
      }
      return `${out} (${id})`;
    };
    const SOURCE_LABEL = { rule: "规则", judge: "评估", admin: "管理员", admin_profile: "管理员资料", tool: "工具", api: "API", set: "设定", undo: "撤销" };
    const sourceLabel = (s) => SOURCE_LABEL[s] || s || "-";

    const undoPreview = ref(null);
    const openUndo = async (r) => {
      const snapshot = scopeSnapshot();
      const revision = scopeRevision.value;
      if (!snapshot) return;
      rememberTrigger();
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
    const closeUndo = () => { undoPreview.value = null; restoreTriggerFocus(); };
    const confirmUndo = async () => {
      const preview = undoPreview.value;
      undoPreview.value = null;
      restoreTriggerFocus();
      if (!preview || !isCurrentScope(preview.snapshot, preview.revision)) return;
      errorMsg.value = "";
      try {
        const data = await scopedPost("undo", { id: preview.id }, preview.snapshot);
        if (!isCurrentScope(preview.snapshot, preview.revision)) return;
        if (data && data.success === false) {
          errorMsg.value = data.error || "撤销失败";
        } else {
          await fetchLogs();
        }
      } catch (e) {
        if (isCurrentScope(preview.snapshot, preview.revision)) errorMsg.value = String(e);
      }
    };

    const selectedLog = ref(null);
    const showDetail = (r) => { rememberTrigger(); selectedLog.value = r; };
    const closeDetail = () => { selectedLog.value = null; restoreTriggerFocus(); };

    // 成员详情弹窗（印象/标签/近期评审）
    const memberDetail = ref(null);
    const memberLoading = ref(false);
    let expiryTimer;
    const clearExpiryTimer = () => { clearTimeout(expiryTimer); expiryTimer = undefined; };
    onUnmounted(clearExpiryTimer);
    const scheduleExpiryRefresh = (member, snapshot, revision, target) => {
      clearExpiryTimer();
      if (!member?.temporary?.active) return;
      const delay = Math.max(0, member.temporary.expires_at * 1000 - Date.now()) + 100;
      expiryTimer = setTimeout(async () => {
        if (!isCurrentMemberRequest(target, revision)) return;
        if (Date.now() < member.temporary.expires_at * 1000) {
          scheduleExpiryRefresh(member, snapshot, revision, target);
          return;
        }
        try {
          await loadMemberRecord(member.group_id, member.user_id, snapshot, revision, target);
          if (isCurrentMemberRequest(target, revision)) await fetchUsers();
        } catch (e) {
          if (isCurrentMemberRequest(target, revision)) setMemberNotice("临时调整已到期，请刷新确认当前关系。" + String(e), true);
        }
      }, Math.min(delay, 2147483647));
    };
    const syncMemberForms = (member) => {
      relationEditMode.value = member?.temporary?.active ? "temporary" : "permanent";
      const m = member || {};
      relationForm.value = {
        familiarity: Number(m.familiarity ?? 0),
        affinity: Number(m.affinity ?? 0),
        trust: Number(m.trust ?? 0),
        reason: "管理员调整",
      };
      profileForm.value = {
        impression: String(m.impression || ""),
        tagsText: Array.isArray(m.tags) ? m.tags.join(", ") : "",
      };
    };
    const loadMemberRecord = async (groupId, userId, snapshot, revision, target) => {
      const data = await scopedGet("member", { group_id: groupId, user_id: userId }, snapshot);
      if (!isCurrentMemberRequest(target, revision)) return false;
      if (data && data.success === false) {
        throw new Error(data.error || "加载成员详情失败");
      }
      memberDetail.value = data.member || null;
      syncMemberForms(memberDetail.value);
      scheduleExpiryRefresh(memberDetail.value, snapshot, revision, target);
      return true;
    };
    const openMember = async (u) => {
      const snapshot = scopeSnapshot();
      const revision = scopeRevision.value;
      if (!snapshot) return;
      setMemberNotice("");
      const candidate = selectedMode.value === "global" ? filterGroup.value : u.group_id;
      const groupId = String(candidate || "");
      if (selectedMode.value !== "global" && !isRealAccount(groupId)) {
        errorMsg.value = "全局空间请先选择群，再查看成员详情";
        return;
      }
      if (selectedMode.value === "global" && groupId && !groups.value.some((g) => g.group_id === groupId)) {
        errorMsg.value = "请选择当前空间中的群";
        return;
      }
      rememberTrigger();
      errorMsg.value = "";
      clearExpiryTimer();
      memberDetail.value = null;
      memberSaveLoading.value = false;
      memberLoading.value = true;
      memberSession += 1;
      const target = { ...snapshot, group_id: groupId, user_id: String(u.user_id), session: memberSession };
      memberRequestTarget.value = target;
      try {
        await loadMemberRecord(groupId, u.user_id, snapshot, revision, target);
      } catch (e) {
        if (isCurrentMemberRequest(target, revision)) errorMsg.value = String(e);
      } finally {
        if (isCurrentMemberRequest(target, revision)) memberLoading.value = false;
      }
    };
    const closeMember = () => {
      clearExpiryTimer();
      memberSession += 1;
      memberRequestTarget.value = null;
      memberDetail.value = null;
      memberLoading.value = false;
      memberSaveLoading.value = false;
      setMemberNotice("");
      restoreTriggerFocus();
    };
    const refreshNames = async (row = null) => {
      const member = row || memberDetail.value, snapshot = scopeSnapshot(), revision = scopeRevision.value;
      const target = memberRequestTarget.value, session = memberSession;
      if (!member || !snapshot || memberSaveLoading.value) return;
      const groupId = row ? (selectedMode.value === "global" ? filterGroup.value : row.group_id) : member.group_id;
      const current = () => isCurrentScope(snapshot, revision) && memberSession === session && (row !== null || isCurrentMemberRequest(target, revision));
      memberSaveLoading.value = true;
      try {
        const data = await scopedPost("member", {group_id:groupId || "",user_id:String(member.user_id)}, snapshot);
        if (!current()) return;
        if (!data.success) throw new Error(data.error || "刷新昵称失败");
        if (!row) memberDetail.value.nickname = data.member.nickname;
        await fetchUsers();
        if (current()) {
          if (row) errorMsg.value = "";
          else setMemberNotice("昵称已刷新");
        }
      } catch (e) {
        if (current()) {
          if (row) errorMsg.value = String(e);
          else setMemberNotice(String(e), true);
        }
      } finally {
        if (current()) memberSaveLoading.value = false;
      }
    };
    const endTemporary = async () => {
      const member=memberDetail.value, snapshot=scopeSnapshot(), revision=scopeRevision.value, target=memberRequestTarget.value;
      if (!member || !snapshot || memberSaveLoading.value) return;
      memberSaveLoading.value=true;
      try {
        const result=await scopedPost("relation",{group_id:member.group_id,user_id:member.user_id,action:"cancel_temporary"},snapshot);
        if (!isCurrentMemberRequest(target,revision)) return;
        if (!result.success) throw new Error(result.error || "结束临时关系失败");
        await loadMemberRecord(member.group_id,member.user_id,snapshot,revision,target);
        if (isCurrentMemberRequest(target,revision)) await fetchUsers();
      } catch(e) { if (isCurrentMemberRequest(target,revision)) setMemberNotice(String(e),true); }
      finally { if (isCurrentMemberRequest(target,revision)) memberSaveLoading.value=false; }
    };
    const saveRelation = async () => {
      const member = memberDetail.value;
      const snapshot = scopeSnapshot();
      const revision = scopeRevision.value;
      if (!member || !snapshot) return;
      const target = { ...snapshot, group_id: String(member.group_id), user_id: String(member.user_id), session: memberSession };
      if (!isCurrentMemberRequest(target, revision)) memberRequestTarget.value = target;
      const familiarity = Number(relationForm.value.familiarity);
      const affinity = Number(relationForm.value.affinity);
      const trust = Number(relationForm.value.trust);
      if (![familiarity, affinity, trust].every(Number.isFinite) ||
          familiarity < 0 || familiarity > 100 ||
          affinity < -100 || affinity > 100 ||
          trust < -100 || trust > 100) {
        setMemberNotice("三维关系数值超出允许范围", true);
        return;
      }
      memberSaveLoading.value = true;
      setMemberNotice("");
      try {
        const data = await scopedPost("relation", {
          group_id: String(member.group_id),
          user_id: String(member.user_id),
          action: relationEditMode.value,
          duration_sec: Number(temporaryMinutes.value) * 60,
          familiarity,
          affinity,
          trust,
          reason: String(relationForm.value.reason || "管理员调整").trim() || "管理员调整",
        }, snapshot);
        if (!isCurrentMemberRequest(target, revision)) return;
        if (data && data.success === false) throw new Error(data.error || "三维关系保存失败");
        if (!await loadMemberRecord(member.group_id, member.user_id, snapshot, revision, target)) return;
        if (!isCurrentMemberRequest(target, revision)) return;
        await Promise.all([fetchUsers(), fetchTodaySummary()]);
        if (!isCurrentMemberRequest(target, revision)) return;
        setMemberNotice("三维关系已保存");
      } catch (e) {
        if (isCurrentMemberRequest(target, revision)) setMemberNotice(`三维关系保存失败：${String(e)}`, true);
      } finally {
        if (isCurrentMemberRequest(target, revision)) memberSaveLoading.value = false;
      }
    };
    const saveProfile = async () => {
      const member = memberDetail.value;
      const snapshot = scopeSnapshot();
      const revision = scopeRevision.value;
      if (!member || !snapshot) return;
      const target = { ...snapshot, group_id: String(member.group_id), user_id: String(member.user_id), session: memberSession };
      if (!isCurrentMemberRequest(target, revision)) memberRequestTarget.value = target;
      memberSaveLoading.value = true;
      setMemberNotice("");
      try {
        const data = await scopedPost("profile", {
          group_id: String(member.group_id),
          user_id: String(member.user_id),
          impression: String(profileForm.value.impression || ""),
          tags: parseTags(profileForm.value.tagsText),
        }, snapshot);
        if (!isCurrentMemberRequest(target, revision)) return;
        if (data && data.success === false) throw new Error(data.error || "印象和标签保存失败");
        if (!await loadMemberRecord(member.group_id, member.user_id, snapshot, revision, target)) return;
        if (!isCurrentMemberRequest(target, revision)) return;
        await fetchUsers();
        if (!isCurrentMemberRequest(target, revision)) return;
        setMemberNotice("印象和标签已保存");
      } catch (e) {
        if (isCurrentMemberRequest(target, revision)) setMemberNotice(`印象和标签保存失败：${String(e)}`, true);
      } finally {
        if (isCurrentMemberRequest(target, revision)) memberSaveLoading.value = false;
      }
    };
    const refreshImpression = async () => {
      const m = memberDetail.value;
      if (!m) return;
      const snapshot = scopeSnapshot();
      const revision = scopeRevision.value;
      if (!snapshot) return;
      const target = { ...snapshot, group_id: String(m.group_id), user_id: String(m.user_id), session: memberSession };
      if (!isCurrentMemberRequest(target, revision)) memberRequestTarget.value = target;
      errorMsg.value = "";
      memberLoading.value = true;
      try {
        const data = await scopedPost("refresh_impression", { group_id: m.group_id, user_id: m.user_id }, snapshot);
        if (!isCurrentMemberRequest(target, revision)) return;
        if (data && data.success === false) {
          errorMsg.value = data.error || data.message || "刷新失败";
        } else {
          await loadMemberRecord(m.group_id, m.user_id, snapshot, revision, target);
          if (!isCurrentMemberRequest(target, revision)) return;
          await fetchUsers();
        }
      } catch (e) {
        if (isCurrentMemberRequest(target, revision)) errorMsg.value = String(e);
      } finally {
        if (isCurrentMemberRequest(target, revision)) memberLoading.value = false;
      }
    };

    const loadPrompts = async (snapshot = scopeSnapshot()) => {
      if (!snapshot) return;
      if (promptLevel.value === "group" && !promptGroupIsValid()) {
        const firstGroup = groups.value.find((g) => isRealAccount(g.group_id));
        if (firstGroup) promptGroupId.value = String(firstGroup.group_id);
      }
      const revision = scopeRevision.value;
      let params;
      promptRequestTarget.value = null;
      try {
        params = promptRequest();
      } catch (e) {
        if (isCurrentScope(snapshot, revision)) setPromptNotice(String(e), true);
        return;
      }
      const target = { ...snapshot, ...params };
      promptRequestTarget.value = target;
      promptLoading.value = true;
      setPromptNotice("");
      try {
        const data = await scopedGet("prompts", params, snapshot);
        if (!isCurrentPromptRequest(target, revision)) return;
        if (data && data.success === false) throw new Error(data.error || "加载提示词设置失败");
        syncPromptForm(data.settings || {}, data.resolved || "");
      } catch (e) {
        if (isCurrentPromptRequest(target, revision)) {
          promptSettings.value = null;
          promptResolved.value = "";
          setPromptNotice(String(e), true);
        }
      } finally {
        if (isCurrentPromptRequest(target, revision)) promptLoading.value = false;
      }
    };

    const savePrompt = async (action) => {
      const snapshot = scopeSnapshot();
      const revision = scopeRevision.value;
      if (!snapshot) return;
      let params;
      promptRequestTarget.value = null;
      try {
        params = promptRequest();
      } catch (e) {
        setPromptNotice(String(e), true);
        return;
      }
      const isReset = action === "reset";
      const body = {
        ...params,
        action,
        mode: isReset ? "default" : promptMode.value,
        template: isReset || promptMode.value === "default" ? "" : String(promptTemplate.value || ""),
      };
      const target = { ...snapshot, ...params };
      promptRequestTarget.value = target;
      promptSaving.value = true;
      setPromptNotice("");
      try {
        const data = await scopedPost("prompts", body, snapshot);
        if (!isCurrentPromptRequest(target, revision)) return;
        if (data && data.success === false) throw new Error(data.error || "提示词操作失败");
        if (action === "preview") {
          promptPreview.value = String(data.preview || "");
          setPromptNotice("预览已更新");
          return;
        }
        await loadPrompts(snapshot);
        if (!isCurrentPromptRequest(target, revision)) return;
        setPromptNotice(action === "reset" ? "已恢复默认提示词" : "提示词已保存");
      } catch (e) {
        if (isCurrentPromptRequest(target, revision)) setPromptNotice(String(e), true);
      } finally {
        if (isCurrentPromptRequest(target, revision)) promptSaving.value = false;
      }
    };

    const modelPayload = () => {
      const raw = normalizeModelConfig(modelConfig.value);
      const payload = {};
      for (const field of evaluationFields) {
        const value = Number(raw[field.key]);
        if (raw[field.key] === "" || raw[field.key] == null || !Number.isFinite(value) || value < field.min || (field.max != null && value > field.max) ||
            (field.step === 1 && !Number.isInteger(value))) {
          throw new Error(`${field.label}须为有效${field.step === 1 ? "整数" : "数值"}，范围 ${field.min}～${field.max ?? "不限"}`);
        }
        payload[field.key] = value;
      }
      const maxConcurrent = Number(maxConcurrentAssessments.value);
      if (!Number.isInteger(maxConcurrent) || maxConcurrent < 1 || maxConcurrent > 32) {
        throw new Error("并发评估上限必须是 1 到 32 的整数");
      }
      return { config: payload, max_concurrent_assessments: maxConcurrent, revision: modelRevision.value };
    };

    const loadEvaluation = async (snapshot = scopeSnapshot()) => {
      if (!snapshot || modelSaving.value) return;
      const revision = scopeRevision.value;
      const requestId = ++evaluationRequestId;
      modelRevision.value = "";
      evaluationLoading.value = true;
      setModelNotice("");
      try {
        const [modelData, statsData] = await Promise.all([
          scopedGet("models", {}, snapshot),
          scopedGet("assessment_stats", {}, snapshot),
        ]);
        if (!isCurrentScope(snapshot, revision) || requestId !== evaluationRequestId) return;
        if (modelData && modelData.success === false) throw new Error(modelData.error || "加载评估配置失败");
        if (statsData && statsData.success === false) throw new Error(statsData.error || "加载评估统计失败");
        modelRevision.value = modelData.revision || "";
        modelConfig.value = normalizeModelConfig(modelData.configured);
        activeModelConfig.value = normalizeModelConfig(modelData.active);
        maxConcurrentAssessments.value = Number(modelData.max_concurrent_assessments || 2);
        activeMaxConcurrentAssessments.value = modelData.active_max_concurrent_assessments ?? null;
        assessmentStats.value = {
          calls: Array.isArray(statsData.stats && statsData.stats.calls) ? statsData.stats.calls : [],
          events: statsData.stats && statsData.stats.events && typeof statsData.stats.events === "object" ? statsData.stats.events : {},
          currency_cost: statsData.stats ? statsData.stats.currency_cost : null,
        };
      } catch (e) {
        if (isCurrentScope(snapshot, revision) && requestId === evaluationRequestId) setModelNotice(String(e), true);
      } finally {
        if (isCurrentScope(snapshot, revision) && requestId === evaluationRequestId) evaluationLoading.value = false;
      }
    };

    const saveModels = async () => {
      const snapshot = scopeSnapshot();
      const revision = scopeRevision.value;
      if (!snapshot || evaluationLoading.value || modelSaving.value) return;
      let body;
      try {
        if (!modelRevision.value) throw new Error("请先刷新评估配置后再保存。");
        body = modelPayload();
      } catch (e) {
        setModelNotice(String(e), true);
        return;
      }
      modelSaving.value = true;
      setModelNotice("");
      try {
        const data = await scopedPost("models", body, snapshot);
        if (!isCurrentScope(snapshot, revision)) return;
        if (data && data.success === false) throw new Error(data.error || "评估配置保存失败");
        modelRevision.value = data.revision || "";
        modelConfig.value = normalizeModelConfig({...modelConfig.value, ...body.config});
        maxConcurrentAssessments.value = body.max_concurrent_assessments;
        setModelNotice(data && data.restart_required
          ? "配置已保存，重载插件后生效。当前生效设置仍显示在右侧。"
          : "评估配置已保存");
      } catch (e) {
        if (isCurrentScope(snapshot, revision)) setModelNotice(String(e), true);
      } finally {
        if (isCurrentScope(snapshot, revision)) modelSaving.value = false;
      }
    };

    const loadScope = async (snapshot = scopeSnapshot()) => {
      if (!snapshot) return;
      const revision = scopeRevision.value;
      scopeLoading.value = true;
      try {
        const [groupData] = await Promise.all([
          scopedGet("groups", {}, snapshot),
          fetchUsers(),
          fetchTodaySummary(),
        ]);
        if (!isCurrentScope(snapshot, revision)) return;
        if (groupData && groupData.success === false) {
          throw new Error(groupData.error || "加载群列表失败");
        }
        groups.value = (groupData && groupData.groups) || [];
        if (tab.value === "logs") await fetchLogs();
        if (tab.value === "prompts") await loadPrompts(snapshot);
        if (tab.value === "evaluation") await loadEvaluation(snapshot);
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
      rememberTrigger();
      restorePreview.value = { backup_name: name, snapshot, revision: scopeRevision.value };
    };
    const closeRestore = () => { restorePreview.value = null; restoreTriggerFocus(); };
    const confirmRestore = async () => {
      const preview = restorePreview.value;
      restorePreview.value = null;
      restoreTriggerFocus();
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

    const logQueryKey = () => JSON.stringify([
      filterGroup.value, filterUser.value.trim(), filterLimit.value, logDimension.value, logSource.value,
    ]);
    const fetchLogs = async () => {
      const snapshot = scopeSnapshot();
      const revision = scopeRevision.value;
      if (!snapshot) return;
      const requestId = ++logRequestId, queryKey = logQueryKey();
      const current = () => isCurrentScope(snapshot, revision) &&
        requestId === logRequestId && queryKey === logQueryKey();
      loading.value = true;
      errorMsg.value = "";
      logs.value = [];
      try {
        const params = { limit: filterLimit.value };
        if (logDimension.value !== "all") params.dimension = logDimension.value;
        if (logSource.value) params.source = logSource.value;
        if (filterGroup.value) params.group_id = filterGroup.value;
        const u = filterUser.value.trim();
        if (u) { params.user_id = u; params.fuzzy = "1"; }
        const data = await scopedGet("logs", params, snapshot);
        if (!current()) return;
        if (data && data.success === false) throw new Error(data.error || "加载失败");
        logs.value = (data && data.logs) || [];
      } catch (e) {
        if (current()) {
          errorMsg.value = String(e);
          logs.value = [];
        }
      } finally {
        if (requestId === logRequestId && isCurrentScope(snapshot, revision)) loading.value = false;
      }
    };
    const showEvaluationChanges = () => {
      userRequestId += 1;
      logSource.value = "judge";
      tab.value = "logs";
      return fetchLogs();
    };

    const fetchTodaySummary = async () => {
      const snapshot = scopeSnapshot(), revision = scopeRevision.value;
      if (!snapshot) return;
      const requestId = ++todayRequestId;
      const current = () => requestId === todayRequestId && isCurrentScope(snapshot, revision);
      todayLoading.value = true;
      todayError.value = "";
      todaySummary.value = null;
      try {
        const data = await scopedGet("today_summary", {}, snapshot);
        if (!current()) return;
        if (!data?.success || !data.summary) throw new Error("今日概览加载失败，请刷新重试。");
        todaySummary.value = data.summary;
      } catch (error) {
        if (current()) todayError.value = "今日概览加载失败，请刷新重试。";
      } finally {
        if (current()) todayLoading.value = false;
      }
    };

    const userQueryKey = () => JSON.stringify([
      filterGroup.value, filterUser.value.trim(), userPage.value,
      userPageSize.value, includeZeroUsers.value,
    ]);
    const fetchUsers = async () => {
      const snapshot = scopeSnapshot();
      const revision = scopeRevision.value;
      if (!snapshot) return;
      const requestId = ++userRequestId;
      const queryKey = userQueryKey();
      const current = () => isCurrentScope(snapshot, revision) &&
        requestId === userRequestId && queryKey === userQueryKey();
      loading.value = true;
      users.value = [];
      errorMsg.value = "";
      try {
        const params = {
          page: userPage.value, page_size: userPageSize.value,
          search: filterUser.value.trim(), include_zero: includeZeroUsers.value ? "1" : "0",
        };
        if (filterGroup.value) params.group_id = filterGroup.value;
        const data = await scopedGet("users", params, snapshot);
        if (!current()) return;
        if (data && data.success === false) throw new Error(data.error || "加载失败");
        users.value = (data && data.users) || [];
        userTotal.value = data.total || 0;
        userMatchedCount.value = data.matched_count || 0;
        userCollapsedCount.value = data.collapsed_count || 0;
        userZeroCount.value = data.zero_count || 0;
        userPages.value = data.pages || 1;
        // 数据减少或临时关系到期后，后端会将越界页修正到最后一页。
        userPage.value = data.page || 1;
      } catch (e) {
        if (current()) {
          errorMsg.value = String(e);
          users.value = [];
          userTotal.value = 0;
          userMatchedCount.value = 0;
          userCollapsedCount.value = 0;
          userZeroCount.value = 0;
          userPages.value = 1;
        }
      } finally {
        if (requestId === userRequestId && isCurrentScope(snapshot, revision)) loading.value = false;
      }
    };
    const resetUserPage = () => { userPage.value = 1; return fetchUsers(); };
    const changeUserPage = (page) => {
      userPage.value = Math.min(userPages.value, Math.max(1, page));
      return fetchUsers();
    };
    const changeListFilter = () => tab.value === "users" ? resetUserPage() : fetchLogs();
    const visibleUsers = computed(() => users.value);

    const refresh = () => {
      if (tab.value === "logs") return fetchLogs();
      if (tab.value === "prompts") return loadPrompts();
      if (tab.value === "evaluation") return loadEvaluation();
      if (tab.value === "settings") return loadSettings();
      return Promise.all([fetchUsers(), fetchTodaySummary()]);
    };
    const switchTab = (t) => {
      if (t !== "users") { userRequestId += 1; loading.value = false; }
      if (t !== "logs") { logRequestId += 1; loading.value = false; }
      tab.value = t;
      errorMsg.value = "";
      if (t === "logs") fetchLogs();
      if (t === "users") { resetUserPage(); fetchTodaySummary(); }
      if (t === "prompts") loadPrompts();
      if (t === "evaluation") loadEvaluation();
      if (t === "settings" && !detailSettings.value && !detailLoading.value) loadSettings();
    };

    const handleKeydown = (e) => {
      if (!e || e.key !== "Escape") return;
      if (undoPreview.value) { closeUndo(); return; }
      if (restorePreview.value) { closeRestore(); return; }
      if (selectedLog.value) { closeDetail(); return; }
      if (memberDetail.value) { closeMember(); }
    };

    onMounted(async () => {
      if (typeof window !== "undefined" && typeof window.addEventListener === "function") {
        window.addEventListener("keydown", handleKeydown);
      }
      try {
        applyHostTheme(await bridge?.ready?.());
      } catch (e) {
        /* ignore */
      }
      await fetchBots();
    });
    onUnmounted(() => {
      if (typeof window !== "undefined" && typeof window.removeEventListener === "function") {
        window.removeEventListener("keydown", handleKeydown);
      }
    });

    return {
      theme, toggleTheme,
      tab, loading, errorMsg, logs, users, visibleUsers, groups,
      todaySummary, todayLoading, todayError, fetchTodaySummary,
      filterGroup, filterUser, filterLimit,
      logDimension, logSource, logDimensions, logDeltaParts, fmtKnownDelta, showEvaluationChanges,
      userPage, userPageSize, userPages, userTotal, userMatchedCount,
      userCollapsedCount, userZeroCount, includeZeroUsers, showUserImpressions, toggleUserImpressions,
      resetUserPage, changeUserPage, changeListFilter,
      bots, selectedBot, selectedMode, selectedBotInfo, scopeLabel,
      scopeLoading, modeLoading, backupLoading, restoreName, latestBackup,
      fmtTime, fmtNum, fmtOptionalNum, fmtSignedNum, dimBarStyle, fmtDelta, fmtIdle, fmtUser, sourceLabel,
      undoPreview, openUndo, closeUndo, confirmUndo,
      selectedLog, showDetail, closeDetail,
      memberDetail, memberLoading, memberNotice, memberNoticeError, memberSaveLoading,
      relationEditMode, temporaryMinutes, endTemporary, relationForm, profileForm, openMember, closeMember, refreshNames, saveRelation, saveProfile, refreshImpression,
      fetchBots, selectBot, applyMode, backupScope, prepareRestore,
      closeRestore, confirmRestore, fetchLogs, fetchUsers, refresh, switchTab,
      restorePreview,
      promptKind, promptLevel, promptGroupId, promptLoading, promptSaving,
      promptMode, promptTemplate, promptResolved, promptPreview, promptNotice, promptNoticeError,
      loadPrompts, savePrompt,
      evaluationLoading, modelSaving, modelConfig, activeModelConfig,
      maxConcurrentAssessments, activeMaxConcurrentAssessments, assessmentStats, modelNotice, modelNoticeError,
      loadEvaluation, saveModels, modelRevision,
      displayReason, displayStatus, detailSettings, detailValues, detailLoading, detailNotice, detailNoticeError, detailGroups, loadSettings, saveSettings,
      restoreSectionDefaults, activeRuleSummary, updateListSetting, listPlaceholder, providerOptions,
      evaluationFields, restoreEvaluationDefaults,
      diagnosticStats, diagnosticLoading, diagnosticNotice, diagnosticDownload, diagnosticConfirm, diagnosticRequest,
    };
  },
  template: `
    <div class="xx-page">
      <header class="xx-header">
        <div class="xx-title">
          <span class="xx-brand-mark" aria-hidden="true">
            <svg viewBox="0 0 24 24"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/><circle cx="18" cy="6" r="1.5" fill="currentColor" stroke="none"/></svg>
          </span>
          <div class="xx-title-text">
            <h2>Bond 关系好感系统</h2>
            <p class="xx-sub">成员羁绊、提示词与互动评估管理</p>
          </div>
        </div>
        <div class="xx-header-actions">
          <nav class="xx-tabs" aria-label="页面导航">
            <button class="xx-tab" :class="{ active: tab === 'users' }" @click="switchTab('users')">当前总览</button>
            <button class="xx-tab" :class="{ active: tab === 'logs' }" @click="switchTab('logs')">变动流水</button>
            <button class="xx-tab" :class="{ active: tab === 'prompts' }" @click="switchTab('prompts')">提示词</button>
            <button class="xx-tab" :class="{ active: tab === 'evaluation' }" @click="switchTab('evaluation')">评估</button>
            <button class="xx-tab" :class="{ active: tab === 'settings' }" @click="switchTab('settings')">详细设置</button>
          </nav>
          <button class="xx-btn xx-btn-mini xx-theme-toggle" @click="toggleTheme" :aria-label="theme === 'dark' ? '切换浅色模式' : '切换深色模式'">{{ theme === 'dark' ? '☀ 浅色模式' : '☾ 深色模式' }}</button>
        </div>
      </header>

      <section class="xx-scope-bar" aria-label="Bot 与数据空间">
        <div class="xx-scope-main">
          <div class="xx-scope-field">
            <label for="xx-bot-select">当前 Bot</label>
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
            <span>当前生效空间</span>
            <strong class="xx-scope-badge" :class="{ 'is-empty': !selectedBot }">{{ selectedBot ? (selectedBot + ' · ' + scopeLabel) : "未选择 Bot" }}</strong>
          </div>
          <div class="xx-scope-actions">
            <button class="xx-btn xx-btn-mini" @click="fetchBots()" :disabled="scopeLoading || modeLoading">刷新 Bot 列表</button>
          </div>
        </div>
        <div v-if="!selectedBot" class="xx-scope-empty-callout">
          <span>尚未发现已连接 Bot，你仍可先配置全局规则与评估模型。</span>
          <button class="xx-btn xx-btn-mini" @click="switchTab('settings')">完成基础设置</button>
        </div>
        <details class="xx-backup-drawer">
          <summary>备份与恢复 <span v-if="latestBackup" class="xx-scope-note">（最近备份：<code>{{ latestBackup }}</code>）</span></summary>
          <div class="xx-backup-body">
            <button class="xx-btn xx-btn-mini" @click="backupScope" :disabled="!selectedBot || backupLoading || scopeLoading">
              {{ backupLoading ? "处理中…" : "备份当前空间" }}
            </button>
            <span v-if="latestBackup" class="xx-scope-note">最近备份：<code>{{ latestBackup }}</code></span>
            <input class="xx-input xx-backup-name" v-model="restoreName" aria-label="备份文件名" placeholder="输入备份文件名以恢复">
            <button class="xx-btn xx-btn-danger xx-btn-mini" @click="prepareRestore" :disabled="!selectedBot || backupLoading || !restoreName.trim()">
              恢复至当前空间
            </button>
          </div>
        </details>
      </section>

      <p v-if="errorMsg" class="xx-error" role="alert">{{ errorMsg }}</p>

      <section v-if="tab === 'users'" class="xx-today-summary" aria-label="今日概览" :aria-busy="todayLoading">
        <div class="xx-today-head">
          <div class="xx-today-title">
            <strong>今日概览</strong>
            <span class="xx-field-help"><button type="button" class="xx-help-button" aria-label="今日概览：统计范围" aria-describedby="today-summary-hint">?</button><span class="xx-help-tooltip" role="tooltip" id="today-summary-hint">初评、复核按所选 Bot 跨群合计，失败调用也计入；UTC 日期 {{ todaySummary ? todaySummary.call_day : '-' }}。变动人数按当前空间的 {{ todaySummary ? todaySummary.relation_timezone : '-' }} 日期 {{ todaySummary ? todaySummary.relation_day : '-' }} 统计，包含实际改分和撤销，不含临时关系与时间衰减；同一成员只算一人，不受列表筛选影响。</span></span>
          </div>
        </div>
        <div v-if="todaySummary" class="xx-today-cards">
          <div class="xx-today-unit">
            <span class="xx-today-unit-label">初评已用 / 限额</span>
            <b class="xx-today-unit-value">{{ todaySummary.initial.used }} <small>/ {{ todaySummary.initial.limit }}</small></b>
          </div>
          <div class="xx-today-unit">
            <span class="xx-today-unit-label">复核已用 / 限额</span>
            <b class="xx-today-unit-value">{{ todaySummary.review.used }} <small>/ {{ todaySummary.review.limit }}</small></b>
          </div>
          <div class="xx-today-unit">
            <span class="xx-today-unit-label">关系变动成员</span>
            <b class="xx-today-unit-value">{{ todaySummary.changed_members }} <small>人</small></b>
          </div>
        </div>
        <div v-else class="xx-today-empty" role="status">{{ !selectedBot ? '选择 Bot 后查看今日统计' : todayLoading ? '加载中…' : todayError || '尚未加载' }}</div>
      </section>

      <div v-if="tab === 'users' || tab === 'logs'" class="xx-filters">
        <select v-model="filterGroup" @change="changeListFilter" aria-label="按群筛选">
          <option value="">全部群</option>
          <option v-for="g in groups" :key="g.group_id" :value="g.group_id">
            {{ g.group_id }}（{{ g.count }}）
          </option>
        </select>
        <input class="xx-input" v-model="filterUser" aria-label="按 QQ 号筛选" placeholder="输入完整或部分 QQ 号" @input="tab === 'users' && resetUserPage()" @keyup.enter="changeListFilter">
        <select v-if="tab === 'logs'" v-model="logDimension" @change="fetchLogs" aria-label="流水维度"><option value="all">全部维度</option><option v-for="item in logDimensions" :key="item.key" :value="item.key">{{ item.label }}</option></select>
        <select v-if="tab === 'logs'" v-model="logSource" @change="fetchLogs" aria-label="流水来源"><option value="">全部来源</option><option value="judge">仅自动评估</option></select>
        <select v-if="tab === 'logs'" v-model="filterLimit" @change="fetchLogs" aria-label="流水条数上限">
          <option :value="100">最近 100</option>
          <option :value="300">最近 300</option>
          <option :value="1000">最近 1000</option>
        </select>
        <button class="xx-btn" @click="refresh" :disabled="loading">
          {{ loading ? "加载中…" : "刷新" }}
        </button>
      </div>

      <div v-if="tab === 'users'" class="xx-member-pagination" aria-label="成员列表分页">
        <span role="status" aria-live="polite">共 {{ userMatchedCount }} 条成员记录，当前显示 {{ userTotal }} 条</span>
        <button v-if="userCollapsedCount" class="xx-btn xx-btn-mini" @click="includeZeroUsers = true; resetUserPage()" :disabled="loading">展开 {{ userCollapsedCount }} 位尚无关系变化的成员</button>
        <button v-else-if="includeZeroUsers && !filterUser.trim()" class="xx-btn xx-btn-mini" @click="includeZeroUsers = false; resetUserPage()" :disabled="loading">折叠尚无关系变化的成员（{{ userZeroCount }}）</button>
        <button type="button" class="xx-btn xx-btn-mini" @click="toggleUserImpressions" :aria-pressed="showUserImpressions">{{ showUserImpressions ? "显示数值" : "显示印象" }}</button>
        <span v-if="filterUser.trim()" class="xx-help">搜索会包含已折叠的成员</span>
        <div class="xx-pagination-nav">
          <label>每页 <select v-model.number="userPageSize" @change="resetUserPage()" :disabled="loading"><option v-for="size in [30, 50, 100, 200]" :key="size" :value="size">{{ size }}</option></select> 条</label>
          <button class="xx-btn xx-btn-mini" @click="changeUserPage(userPage - 1)" :disabled="loading || userPage <= 1">上一页</button>
          <span>第 {{ userPage }} / {{ userPages }} 页</span>
          <button class="xx-btn xx-btn-mini" @click="changeUserPage(userPage + 1)" :disabled="loading || userPage >= userPages">下一页</button>
        </div>
      </div>

      <!-- 当前总览 -->
      <div v-if="tab === 'users'" class="xx-table-wrap">
        <table class="xx-table xx-overview-table" :class="{ 'is-impressions': showUserImpressions }">
          <colgroup v-if="showUserImpressions">
            <col class="xx-overview-col-member"><col class="xx-overview-col-relation"><col class="xx-overview-col-status"><col class="xx-overview-col-tags"><col><col class="xx-overview-col-actions">
          </colgroup>
          <thead>
            <tr>
              <th>成员 / QQ</th><th>关系称谓</th><th v-if="!showUserImpressions">熟悉度</th><th v-if="!showUserImpressions">亲近度</th><th v-if="!showUserImpressions">信任度</th><th>状态</th><th>标签</th><th v-if="showUserImpressions">印象</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!visibleUsers.length">
              <td :colspan="showUserImpressions ? 6 : 8" class="xx-empty">{{ loading ? "加载中…" : (userCollapsedCount ? "尚无关系变化的成员已折叠，可在上方展开" : "暂无匹配成员") }}</td>
            </tr>
            <tr v-for="u in visibleUsers" :key="u.group_id + '_' + u.user_id">
              <td class="xx-overview-member-cell" :title="(u.nickname || '昵称未知') + '（' + u.user_id + '）'">
                <div class="xx-user-cell">
                  <span class="xx-user-nick" :class="{ 'is-unknown': !u.nickname }">{{ u.nickname || "昵称未知" }}</span>
                  <span class="xx-user-id xx-mono">QQ {{ u.user_id }}</span>
                </div>
              </td>
              <td class="xx-overview-relation-cell"><span class="xx-title-badge">{{ u.relation_title || "-" }}</span></td>
              <td v-if="!showUserImpressions">
                <div class="xx-dim-cell">
                  <div class="xx-dim-head">
                    <span class="xx-dim-badge xx-dim-f">F</span>
                    <span class="xx-dim-val">{{ fmtOptionalNum(u.familiarity) }}</span>
                  </div>
                  <div class="xx-dim-track" aria-hidden="true"><i class="xx-dim-fill is-f" :style="dimBarStyle('familiarity', u.familiarity)"></i></div>
                </div>
              </td>
              <td v-if="!showUserImpressions">
                <div class="xx-dim-cell">
                  <div class="xx-dim-head">
                    <span class="xx-dim-badge xx-dim-a">A</span>
                    <span class="xx-dim-val" :class="u.affinity > 0 ? 'xx-up' : (u.affinity < 0 ? 'xx-down' : 'xx-zero')">{{ fmtSignedNum(u.affinity) }}</span>
                  </div>
                  <div class="xx-dim-track is-signed" aria-hidden="true"><i class="xx-dim-fill" :class="u.affinity < 0 ? 'is-neg' : 'is-a-pos'" :style="dimBarStyle('affinity', u.affinity)"></i></div>
                </div>
              </td>
              <td v-if="!showUserImpressions">
                <div class="xx-dim-cell">
                  <div class="xx-dim-head">
                    <span class="xx-dim-badge xx-dim-t">T</span>
                    <span class="xx-dim-val" :class="u.trust > 0 ? 'xx-up' : (u.trust < 0 ? 'xx-down' : 'xx-zero')">{{ fmtSignedNum(u.trust) }}</span>
                  </div>
                  <div class="xx-dim-track is-signed" aria-hidden="true"><i class="xx-dim-fill" :class="u.trust < 0 ? 'is-neg' : 'is-t-pos'" :style="dimBarStyle('trust', u.trust)"></i></div>
                </div>
              </td>
              <td class="xx-overview-status-cell">
                <span v-if="u.decayed" class="xx-tag">衰减中</span>
                <span v-else-if="u.idle_days != null && u.idle_days >= 3" class="xx-tag">{{ u.idle_days }}天未动</span>
                <span v-else class="xx-muted-text">-</span>
              </td>
              <td class="xx-imp-cell" data-label="标签">
                <div class="xx-imp">
                  <div v-if="u.tags && u.tags.length" class="xx-chips">
                    <span v-for="t in u.tags" :key="t" class="xx-chip">{{ t }}</span>
                  </div>
                  <span v-if="!(u.tags && u.tags.length)" class="xx-muted-text">-</span>
                </div>
              </td>
              <td v-if="showUserImpressions" class="xx-overview-impression-cell" data-label="印象">
                <p class="xx-overview-impression" :class="{ 'xx-muted-text': !u.impression || !u.impression.trim() }">{{ u.impression && u.impression.trim() ? u.impression : "暂无印象" }}</p>
              </td>
              <td class="xx-actions">
                <button class="xx-btn xx-btn-mini" @click="openMember(u)" title="查看成员详情">详情</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <p v-if="tab === 'logs'" class="xx-help" style="margin: 0 0 12px;">筛选后只显示该维度发生变化的记录。旧记录缺失的数值显示为未知；撤销会恢复整条记录涉及的所有维度。</p>
      <!-- 变动流水 -->
      <div v-if="tab === 'logs'" class="xx-table-wrap">
        <table class="xx-table">
          <thead>
            <tr>
              <th>时间</th><th>群</th><th>QQ</th><th>关系变化</th><th>亲近度前后</th><th>原因</th><th>来源</th><th>操作</th>
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
              <td><div class="xx-relation-3d"><span v-for="change in logDeltaParts(r)" :key="change.key" class="xx-log-delta" :class="'xx-log-delta-' + change.key">{{ change.label }} {{ fmtKnownDelta(change.value) }}</span></div></td>
              <td class="xx-mono">{{ fmtOptionalNum(r.favor_before) }} → {{ fmtOptionalNum(r.favor_after) }}</td>
              <td class="xx-reason-cell" :title="displayReason(r.reason)">{{ displayReason(r.reason) }}</td>
              <td><span class="xx-tag">{{ sourceLabel(r.source) }}</span></td>
              <td class="xx-actions">
                <button class="xx-btn xx-btn-mini" @click="showDetail(r)">详情</button>
                <span v-if="r.reversed" class="xx-tag xx-muted">已撤销</span>
                <span v-else-if="r.source === 'admin_profile'" class="xx-tag xx-muted">资料变更不可撤销</span>
                <button v-else class="xx-btn xx-btn-undo" @click="openUndo(r)" :disabled="loading">撤销</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 提示词设置 -->
      <section v-if="tab === 'prompts'" class="xx-panel">
        <div class="xx-panel-head">
          <div>
            <h3>提示词设置</h3>
            <p class="xx-help">全局设置对所有 Bot 生效；指定群按当前 Bot 和所选群保存，保存后即时生效。</p>
          </div>
          <button class="xx-btn xx-btn-mini" @click="loadPrompts()" :disabled="promptLoading || promptSaving">
            {{ promptLoading ? "加载中…" : "刷新" }}
          </button>
        </div>
        <div class="xx-settings-toolbar">
          <label class="xx-scope-field">
            <span>提示词类型</span>
            <select v-model="promptKind" @change="loadPrompts()" :disabled="promptLoading || promptSaving">
              <option value="behavior">关系行为</option>
              <option value="assessment">互动评估</option>
            </select>
          </label>
          <label class="xx-scope-field">
            <span>适用范围</span>
            <select v-model="promptLevel" @change="loadPrompts()" :disabled="promptLoading || promptSaving">
              <option value="global">全局</option>
              <option value="group">指定群</option>
            </select>
          </label>
          <label v-if="promptLevel === 'group'" class="xx-scope-field">
            <span>选择群</span>
            <select v-model="promptGroupId" @change="loadPrompts()" :disabled="promptLoading || promptSaving || !groups.length">
              <option value="">选择群</option>
              <option v-for="g in groups" :key="g.group_id" :value="g.group_id">{{ g.group_id }}</option>
            </select>
          </label>
        </div>
        <p v-if="promptNotice" :class="['xx-status', promptNoticeError ? 'xx-status-error' : 'xx-status-ok']">{{ promptNotice }}</p>
        <div class="xx-settings-grid">
          <section class="xx-form-card">
            <h4>编辑模板</h4>
            <p class="xx-muted-text">填写你希望模型如何判断互动即可。</p>
            <label class="xx-form-row">
              <span>模式</span>
              <select v-model="promptMode" :disabled="promptLoading || promptSaving">
                <option value="default">默认</option>
                <option value="custom">自定义</option>
              </select>
            </label>
            <label class="xx-form-row xx-form-row-block">
              <span>模板内容</span>
              <textarea class="xx-textarea xx-prompt-editor" v-model="promptTemplate" :disabled="promptMode === 'default' || promptLoading || promptSaving" placeholder="自定义模式下填写模板"></textarea>
            </label>
            <p v-if="promptKind === 'behavior'" class="xx-muted-text">行为模板可用占位符：<code>{relationship}</code>、<code>{impression}</code>、<code>{events}</code></p>
            <div class="xx-modal-actions">
              <button class="xx-btn xx-btn-mini" @click="savePrompt('reset')" :disabled="promptLoading || promptSaving">恢复默认</button>
              <button class="xx-btn xx-btn-mini" @click="savePrompt('preview')" :disabled="promptLoading || promptSaving">预览未保存内容</button>
              <button class="xx-btn xx-btn-primary" @click="savePrompt('save')" :disabled="promptLoading || promptSaving">
                {{ promptSaving ? "保存中…" : "保存模板" }}
              </button>
            </div>
          </section>
          <section class="xx-form-card">
            <h4>当前生效内容</h4>
            <p class="xx-muted-text"><span class="xx-tag">{{ promptSettings && promptSettings.effective ? ('来源：' + displayStatus(promptSettings.effective.source)) : '全局设置' }}</span></p>
            <pre class="xx-preview">{{ promptResolved || "暂无内容" }}</pre>
            <div v-if="promptPreview" class="xx-preview-block">
              <h4>草稿预览</h4>
              <pre class="xx-preview">{{ promptPreview }}</pre>
            </div>
          </section>
        </div>
      </section>

      <!-- 评估配置与统计 -->
      <section v-if="tab === 'evaluation'" class="xx-panel">
        <div class="xx-panel-head">
          <div>
            <h3>互动评估</h3>
            <p class="xx-help">设置对所有 Bot 生效，保存后重载插件。</p>
          </div>
          <button class="xx-btn xx-btn-mini" @click="loadEvaluation()" :disabled="evaluationLoading || modelSaving">
            {{ evaluationLoading ? "加载中…" : "刷新" }}
          </button>
        </div>
        <p v-if="modelNotice" :class="['xx-status', modelNoticeError ? 'xx-status-error' : 'xx-status-ok']">{{ modelNotice }}</p>
        <div class="xx-member-pagination">
          <label>按维度查看评估变动 <select v-model="logDimension"><option value="all">全部维度</option><option v-for="item in logDimensions" :key="item.key" :value="item.key">{{ item.label }}</option></select></label>
          <button class="xx-btn xx-btn-mini" @click="showEvaluationChanges()">查看评估流水</button>

        </div>
        <div class="xx-settings-grid xx-evaluation-grid">
          <details class="xx-form-card xx-evaluation-settings">
            <summary>评估高级设置</summary>
            <p class="xx-help">开关和模型在“详细设置 → 基础设置”中选择。</p>
            <div v-for="field in evaluationFields" :key="field.key" class="xx-form-row">
              <span class="xx-field-label"><label :for="'evaluation-' + field.key">{{ field.label }}</label>
                <span class="xx-field-help"><button type="button" class="xx-help-button" :aria-label="field.label + '：说明'" :aria-describedby="'eval-hint-' + field.key">?</button><span class="xx-help-tooltip" role="tooltip" :id="'eval-hint-' + field.key">{{ field.hint }}</span></span>
              </span>
              <input :id="'evaluation-' + field.key" class="xx-input" type="number" :min="field.min" :max="field.max" :step="field.step" v-model.number="modelConfig[field.key]" :disabled="evaluationLoading || modelSaving">
            </div>
            <div class="xx-form-row">
              <span class="xx-field-label"><label for="evaluation-concurrent">同时评估上限</label>
                <span class="xx-field-help"><button type="button" class="xx-help-button" aria-label="同时评估上限：说明" aria-describedby="eval-hint-concurrent">?</button><span class="xx-help-tooltip" role="tooltip" id="eval-hint-concurrent">所有 Bot 同时进行的评估总数。达到上限时跳过新评估，聊天照常进行。</span></span>
              </span>
              <input id="evaluation-concurrent" class="xx-input" type="number" min="1" max="32" step="1" v-model.number="maxConcurrentAssessments" :disabled="evaluationLoading || modelSaving">
            </div>
            <div class="xx-modal-actions">
              <button class="xx-btn xx-btn-mini" @click="restoreEvaluationDefaults()" :disabled="evaluationLoading || modelSaving">恢复此区默认设置</button>
              <button class="xx-btn xx-btn-primary" @click="saveModels" :disabled="evaluationLoading || modelSaving || !modelRevision">
                {{ modelSaving ? "保存中…" : "保存配置" }}
              </button>
            </div>
          </details>
          <section class="xx-form-card">
            <h4>当前生效设置</h4>
            <dl class="xx-config-list">
              <div><dt>状态</dt><dd>{{ activeModelConfig.enabled ? "已启用" : "未启用" }}</dd></div>
              <div><dt>日常模型</dt><dd class="xx-mono">{{ activeModelConfig.small_provider_id || "未设置" }}</dd></div>
              <div><dt>复核模型</dt><dd class="xx-mono">{{ activeModelConfig.large_provider_id || "未设置" }}</dd></div>
              <div><dt>模式</dt><dd>{{ activeModelConfig.assessment_mode === 'single' ? "单模型" : "分级复核" }}</dd></div>
              <div><dt>超时</dt><dd>{{ activeModelConfig.small_timeout_sec || "-" }} / {{ activeModelConfig.large_timeout_sec || "-" }} 秒</dd></div>
              <div><dt>初评频率限制</dt><dd>{{ activeModelConfig.window_sec }} 秒 / {{ activeModelConfig.window_max_calls }} 次</dd></div>
              <div><dt>每日初评 / 复核（UTC）</dt><dd>个人 {{ activeModelConfig.per_user_daily_small_cap }} / {{ activeModelConfig.per_user_daily_large_cap }}；Bot {{ activeModelConfig.per_bot_daily_small_cap }} / {{ activeModelConfig.per_bot_daily_large_cap }}</dd></div>
              <div><dt>参考上下文</dt><dd>{{ activeModelConfig.context_turns }} 条 / {{ activeModelConfig.context_chars }} 字符；摘要 {{ activeModelConfig.context_days }} 天 / {{ activeModelConfig.context_events }} 条</dd></div>
              <div><dt>同时评估人数</dt><dd>{{ activeMaxConcurrentAssessments ?? "未知" }}</dd></div>
            </dl>
          </section>
        </div>
        <section class="xx-stats-section">
          <div class="xx-panel-head"><h3>调用统计</h3><span class="xx-muted-text">费用：{{ assessmentStats.currency_cost == null ? "未提供" : assessmentStats.currency_cost }}</span></div>
          <div class="xx-stats-grid">
            <div class="xx-stat-box"><span>调用分组</span><b>{{ assessmentStats.calls.length }}</b></div>
            <div class="xx-stat-box"><span>事件状态</span><b>{{ Object.keys(assessmentStats.events).length }}</b></div>
          </div>
          <div class="xx-table-wrap">
            <table class="xx-table">
              <thead><tr><th>日期</th><th>群</th><th>层级</th><th>状态</th><th>次数</th><th>Token</th></tr></thead>
              <tbody>
                <tr v-if="!assessmentStats.calls.length"><td colspan="6" class="xx-empty">暂无调用记录</td></tr>
                <tr v-for="call in assessmentStats.calls" :key="call.day + call.group_id + call.tier + call.status">
                  <td class="xx-mono">{{ call.day }}</td><td class="xx-mono">{{ call.group_id }}</td><td>{{ displayStatus(call.tier) }}</td><td>{{ displayStatus(call.status) }}</td><td>{{ call.count }}</td><td>{{ call.tokens == null ? "未提供" : call.tokens }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div class="xx-event-list">
            <span v-for="(count, status) in assessmentStats.events" :key="status" class="xx-tag">{{ displayStatus(status) }}：{{ count }}</span>
          </div>
        </section>
      </section>

      <section v-if="tab === 'settings'" class="xx-panel">
        <h3>详细设置</h3>
        <p class="xx-help">设置适用于所有 Bot，保存并重载插件后生效。</p>
        <p v-if="detailNotice" role="status" :class="['xx-status', detailNoticeError ? 'xx-status-error' : 'xx-status-ok']">{{ detailNotice }}</p>
        <div class="xx-settings-toolbar xx-detail-toolbar">
          <button class="xx-btn xx-btn-mini" @click="loadSettings()" :disabled="detailLoading">{{ detailLoading ? "处理中…" : "刷新设置" }}</button>
          <button class="xx-btn xx-btn-primary" @click="saveSettings()" :disabled="detailLoading || !detailSettings">保存修改</button>
        </div>
        <div class="xx-form-card" v-if="activeRuleSummary">
          <h4>当前关系设置 · {{ activeRuleSummary.mode }}</h4>
          <div class="xx-table-wrap xx-rule-summary">
            <table class="xx-table" aria-label="当前关系设置">
              <thead><tr><th scope="col">维度</th><th scope="col">每日加分 / 扣分上限</th><th scope="col">衰减速度</th><th scope="col">基线</th></tr></thead>
              <tbody><tr v-for="cap in activeRuleSummary.caps" :key="cap.key">
                <th scope="row">{{ cap.label }}</th><td>+{{ cap.up }} / −{{ cap.down }}</td><td>{{ activeRuleSummary[cap.key] }}</td>
                <td>{{ cap.key !== 'familiarity' && activeRuleSummary.baselines[cap.key] > 0 ? '±' : '' }}{{ activeRuleSummary.baselines[cap.key] }}</td>
              </tr></tbody>
            </table>
          </div>
        </div>
        <details v-for="group in detailGroups" :key="group.group" class="xx-form-card">
          <summary>{{ group.title }}</summary>
          <p v-if="group.group === 'rules'" class="xx-help">
            <span class="xx-field-help"><button type="button" class="xx-help-button" aria-label="关系变化：额度与区间说明" aria-describedby="rules-hint">?</button><span class="xx-help-tooltip" role="tooltip" id="rules-hint">加分和扣分分别累计，时间衰减不占额度。倍率区间按默认分界标注；修改过分界时，以自定义分界为准。</span></span>
          </p>
          <button v-if="group.group !== 'basic'" class="xx-btn xx-btn-mini" style="margin-top: 8px;" @click="restoreSectionDefaults(group.group)" :disabled="detailLoading">恢复此区默认设置</button>
          <div v-for="field in group.fields" :key="field.path" class="xx-form-row xx-detail-row">
            <span class="xx-field-label"><label :for="'setting-' + field.path">{{ field.label }}</label>
              <span class="xx-field-help"><button type="button" class="xx-help-button" :aria-label="field.label + '：说明'" :aria-describedby="'hint-' + field.path">?</button><span class="xx-help-tooltip" role="tooltip" :id="'hint-' + field.path">{{ field.hint }}</span></span>
            </span>
            <input :id="'setting-' + field.path" v-if="field.type === 'bool'" type="checkbox" v-model="detailValues[field.path]" :disabled="detailLoading">
            <div v-else-if="field.special === 'select_provider'" class="xx-provider-field">
              <select :id="'setting-' + field.path" class="xx-input" v-model="detailValues[field.path]" :disabled="detailLoading" :aria-describedby="'provider-hint-' + field.path">
                <option value="">{{ field.path === 'assessment.large_provider_id' ? '不使用复核模型' : '选择模型' }}</option>
                <option v-for="provider in providerOptions(field.path)" :key="provider.id" :value="provider.id">{{ provider.label }}</option>
              </select>
              <p v-if="!detailSettings.providers?.length" class="xx-help" :id="'provider-hint-' + field.path">暂无可用模型，请先在 AstrBot 配置文本模型。</p>
            </div>
            <select :id="'setting-' + field.path" v-else-if="field.options?.length" class="xx-input" v-model="detailValues[field.path]" :disabled="detailLoading"><option v-for="option in field.options" :key="option" :value="option">{{ ({default:"标准",galgame:"Galgame · 正向×2",realistic:"基础",single:"单模型",tiered:"分级复核",all:"所有人",master:"仅主人",per_group:"按群分别记录",global:"同一 Bot 跨群共用"})[option] || option }}</option></select>
            <input :id="'setting-' + field.path" v-else-if="field.type === 'int' || field.type === 'float'" class="xx-input" type="number" :step="field.type === 'int' ? 1 : 'any'" :min="field.path.endsWith('_baseline') ? 0 : undefined" :max="field.path.endsWith('_baseline') ? 100 : undefined" v-model.number="detailValues[field.path]" :disabled="detailLoading">
            <input :id="'setting-' + field.path" v-else-if="field.type === 'list'" class="xx-input" :value="(detailValues[field.path] || []).join(', ')" @change="updateListSetting(field.path, $event.target.value)" :disabled="detailLoading" :placeholder="listPlaceholder(field.path)">
            <input :id="'setting-' + field.path" v-else class="xx-input" v-model="detailValues[field.path]" :disabled="detailLoading" :placeholder="field.path === 'master_ids' ? listPlaceholder(field.path) : ''">
          </div>
        </details>
        <div class="xx-form-card">
          <h4>当前空间的诊断记录</h4>
          <p class="xx-help">开启“关系诊断记录”后可导出本空间的运行记录。最多保留7天、合计20MB，不保存聊天原文，不自动上传。</p>
          <div class="xx-actions" style="margin-top: 12px;">
            <button class="xx-btn xx-btn-mini" @click="diagnosticRequest('status')" :disabled="diagnosticLoading">刷新记录状态</button>
            <button class="xx-btn xx-btn-mini" @click="diagnosticRequest('export')" :disabled="diagnosticLoading">准备导出</button>
            <button class="xx-btn" :class="diagnosticConfirm ? 'xx-btn-danger' : 'xx-btn-mini'" @click="diagnosticRequest('clear')" :disabled="diagnosticLoading">{{ diagnosticConfirm ? "确认清除此空间的诊断记录" : "清除诊断记录" }}</button>
            <a v-if="diagnosticDownload" :href="diagnosticDownload" download="bond-diagnostics.json" class="xx-btn xx-btn-primary">下载脱敏记录</a>
          </div>
          <p v-if="diagnosticNotice" class="xx-help" role="status">{{ diagnosticNotice }}</p>
          <dl v-if="diagnosticStats" class="xx-runtime-list" style="margin-top: 12px;">
            <div><dt>当前记录开关</dt><dd>{{ diagnosticStats.enabled ? "已开启" : "已关闭" }}</dd></div>
            <div><dt>本次运行已记录</dt><dd>{{ diagnosticStats.counters?.written || 0 }} 条</dd></div>
            <div><dt>等待写入</dt><dd>{{ diagnosticStats.queued || 0 }} 条</dd></div>
            <div><dt>丢弃或写入失败</dt><dd>{{ diagnosticStats.counters?.dropped || 0 }} / {{ diagnosticStats.counters?.write_errors || 0 }}</dd></div>
            <div><dt>记录完整性</dt><dd>{{ diagnosticStats.gap ? "存在记录缺口，请查看导出说明" : "本次运行未发现缺失，历史记录完整性未知" }}</dd></div>
          </dl>
        </div>
      </section>

      <!-- 变动详情弹窗：完整发言 + AI 原因 -->
      <div v-if="selectedLog" class="xx-modal" role="dialog" aria-modal="true" aria-labelledby="xx-log-modal-title" @click.self="closeDetail">
        <div class="xx-modal-box">
          <header class="xx-modal-header">
            <span id="xx-log-modal-title">变动详情</span>
            <button type="button" class="xx-modal-close" aria-label="关闭变动详情" @click="closeDetail">×</button>
          </header>
          <div class="xx-modal-meta xx-mono">
            <span>{{ fmtTime(selectedLog.ts) }} · 群 {{ selectedLog.group_id }} · QQ {{ selectedLog.user_id }}
              <span v-for="change in logDeltaParts(selectedLog, 'all')" :key="change.key"> · {{ change.label }} {{ fmtKnownDelta(change.value) }}</span>
              · 亲近度 {{ fmtOptionalNum(selectedLog.favor_before) }} → {{ fmtOptionalNum(selectedLog.favor_after) }}
            </span>
            <span class="xx-tag">{{ sourceLabel(selectedLog.source) }}</span>
          </div>
          <section class="xx-modal-section">
            <h4>用户发言</h4>
            <p class="xx-modal-text">{{ selectedLog.message || "（没有相关发言）" }}</p>
          </section>
          <section class="xx-modal-section">
            <h4>变动原因</h4>
            <p class="xx-modal-text">{{ displayReason(selectedLog.reason) }}</p>
          </section>
          <div class="xx-modal-actions">
            <button type="button" class="xx-btn xx-btn-mini" @click="closeDetail">关闭</button>
          </div>
        </div>
      </div>

      <!-- 成员详情弹窗：三维关系 + profile 编辑 + 可展开历史 -->
      <div v-if="memberDetail" class="xx-modal" role="dialog" aria-modal="true" aria-labelledby="xx-member-modal-title" @click.self="closeMember">
        <div class="xx-modal-box xx-modal-box-wide">
          <header class="xx-modal-header">
            <span id="xx-member-modal-title">成员详情</span>
            <button type="button" class="xx-modal-close" aria-label="关闭成员详情" @click="closeMember">×</button>
          </header>
          <div class="xx-modal-meta">
            <div class="xx-member-identity">
              <span class="xx-member-identity-name">{{ memberDetail.nickname || "昵称未知" }}</span>
              <span class="xx-member-identity-sub xx-mono">QQ {{ memberDetail.user_id }} · 群 {{ memberDetail.group_id || "全局共享" }}</span>
            </div>
            <div class="xx-member-meta-actions">
              <span class="xx-member-last-interaction">最近互动：{{ fmtIdle(memberDetail.idle_days) }}</span>
              <button class="xx-btn xx-btn-mini" @click="refreshNames()" :disabled="memberSaveLoading">刷新昵称</button>
            </div>
          </div>
          <div class="xx-member-stats">
            <div class="xx-member-stat">
              <label><span class="xx-dim-badge xx-dim-f">F</span> 熟悉度</label>
              <b>{{ fmtOptionalNum(memberDetail.familiarity) }}</b>
              <div class="xx-dim-track" style="width:100%" aria-hidden="true"><i class="xx-dim-fill is-f" :style="dimBarStyle('familiarity', memberDetail.familiarity)"></i></div>
            </div>
            <div class="xx-member-stat">
              <label><span class="xx-dim-badge xx-dim-a">A</span> 亲近度</label>
              <b :class="memberDetail.affinity > 0 ? 'xx-up' : (memberDetail.affinity < 0 ? 'xx-down' : 'xx-zero')">{{ fmtSignedNum(memberDetail.affinity) }}</b>
              <div class="xx-dim-track is-signed" style="width:100%" aria-hidden="true"><i class="xx-dim-fill" :class="memberDetail.affinity < 0 ? 'is-neg' : 'is-a-pos'" :style="dimBarStyle('affinity', memberDetail.affinity)"></i></div>
            </div>
            <div class="xx-member-stat">
              <label><span class="xx-dim-badge xx-dim-t">T</span> 信任度</label>
              <b :class="memberDetail.trust > 0 ? 'xx-up' : (memberDetail.trust < 0 ? 'xx-down' : 'xx-zero')">{{ fmtSignedNum(memberDetail.trust) }}</b>
              <div class="xx-dim-track is-signed" style="width:100%" aria-hidden="true"><i class="xx-dim-fill" :class="memberDetail.trust < 0 ? 'is-neg' : 'is-t-pos'" :style="dimBarStyle('trust', memberDetail.trust)"></i></div>
            </div>
            <div class="xx-member-stat">
              <label>关系称谓</label>
              <div><span class="xx-title-badge">{{ memberDetail.relation_title || "-" }}</span></div>
            </div>
          </div>
          <div v-if="memberDetail.temporary && memberDetail.temporary.active" class="xx-temp-banner" role="status">
            <p><strong>临时关系生效中</strong> · 截至 {{ fmtTime(memberDetail.temporary.expires_at) }}。评估、印象更新和正式关系衰减已暂停。</p>
            <div class="xx-temp-formal-pill">正式值保留：熟悉度 {{ memberDetail.formal_relation.familiarity }} · 亲近度 {{ memberDetail.formal_relation.affinity }} · 信任度 {{ memberDetail.formal_relation.trust }}</div>
            <div><button class="xx-btn xx-btn-mini" @click="endTemporary" :disabled="memberSaveLoading">立即结束临时关系</button></div>
          </div>
          <p v-if="memberNotice" role="status" :class="['xx-status', memberNoticeError ? 'xx-status-error' : 'xx-status-ok']">{{ memberNotice }}</p>
          <div class="xx-modal-edit-grid">
          <section class="xx-modal-section xx-edit-section">
            <h4>编辑三维关系</h4>
            <label class="xx-form-row"><span>调整方式</span><select v-model="relationEditMode"><option value="permanent" :disabled="memberDetail.temporary && memberDetail.temporary.active">正式调整</option><option value="temporary">临时调整</option></select></label>
            <label v-if="relationEditMode === 'temporary'" class="xx-form-row"><span>持续时间（分钟，1～43200）</span><input class="xx-input" type="number" min="1" max="43200" v-model.number="temporaryMinutes"></label>
            <p v-if="relationEditMode === 'temporary'" class="xx-help">临时覆盖三个维度，称谓和排行随之变化；到期恢复正式关系，不补评期间消息。修改临时值会重新计算截止时间。</p>
            <div class="xx-edit-grid">
              <label><span>熟悉度 (0~100)</span><input class="xx-input" type="number" min="0" max="100" step="0.1" v-model.number="relationForm.familiarity"></label>
              <label><span>亲近度 (±100)</span><input class="xx-input" type="number" min="-100" max="100" step="0.1" v-model.number="relationForm.affinity"></label>
              <label><span>信任度 (±100)</span><input class="xx-input" type="number" min="-100" max="100" step="0.1" v-model.number="relationForm.trust"></label>
            </div>
            <label class="xx-form-row xx-form-row-block"><span>变更原因</span><input class="xx-input" style="width: 100%;" v-model="relationForm.reason" placeholder="管理员调整"></label>
            <div class="xx-modal-actions"><button class="xx-btn xx-btn-primary" @click="saveRelation" :disabled="memberSaveLoading">{{ relationEditMode === 'temporary' ? '保存临时关系' : '保存正式关系' }}</button></div>
          </section>
          <section class="xx-modal-section xx-edit-section">
            <h4>编辑印象与标签</h4>
            <p v-if="!memberDetail.group_id" class="xx-help">请先选择群再修改群资料。</p>
            <label class="xx-form-row xx-form-row-block"><span>印象</span><textarea class="xx-textarea" v-model="profileForm.impression" placeholder="成员印象"></textarea></label>
            <label class="xx-form-row xx-form-row-block"><span>标签</span><input class="xx-input" style="width: 100%;" v-model="profileForm.tagsText" placeholder="用逗号分隔多个标签"></label>
            <div class="xx-modal-actions">
              <button class="xx-btn xx-btn-mini" @click="refreshImpression" :disabled="memberLoading || memberSaveLoading || !memberDetail.group_id || memberDetail.temporary?.active">{{ memberLoading ? "处理中…" : "刷新印象" }}</button>
              <button class="xx-btn xx-btn-primary" @click="saveProfile" :disabled="memberSaveLoading || !memberDetail.group_id">{{ memberSaveLoading ? "保存中…" : "保存印象" }}</button>
            </div>
          </section>
          </div>
          <details v-if="memberDetail.temporary_audit && memberDetail.temporary_audit.length" class="xx-member-history"><summary>临时关系操作记录（{{ memberDetail.temporary_audit.length }}）</summary><p v-for="(item,i) in memberDetail.temporary_audit" :key="i" class="xx-help">{{ fmtTime(item.ts) }} · {{ item.actor }} · {{ ({created:'创建',updated:'修改',cancelled:'结束',expired:'到期'})[item.action] || item.action }}<span v-if="item.details.values"> · 熟悉度 {{ item.details.values.familiarity }}，亲近度 {{ item.details.values.affinity }}，信任度 {{ item.details.values.trust }} · 截至 {{ fmtTime(item.details.expires_at) }} · {{ item.details.reason }}</span></p></details>
          <details class="xx-member-history">
            <summary>近期评审（{{ memberDetail.recent_judges ? memberDetail.recent_judges.length : 0 }}）</summary>
            <p v-if="!memberDetail.recent_judges || !memberDetail.recent_judges.length" class="xx-modal-text xx-muted-text">暂无评估记录</p>
            <ul v-else class="xx-judge-list">
              <li v-for="(j, i) in memberDetail.recent_judges" :key="i">
                <span class="xx-mono">{{ fmtTime(j.ts) }}</span>
                <span :class="j.delta > 0 ? 'xx-up' : (j.delta < 0 ? 'xx-down' : 'xx-zero')">{{ fmtDelta(j.delta) }}</span>
                <span class="xx-judge-reason">{{ displayReason(j.reason) }}</span>
              </li>
            </ul>
          </details>
          <div class="xx-modal-actions">
            <button type="button" class="xx-btn xx-btn-mini" @click="closeMember">关闭</button>
          </div>
        </div>
      </div>

      <!-- 撤销确认弹窗：预览撤销后的旧亲近度 -->
      <div v-if="undoPreview" class="xx-modal" role="dialog" aria-modal="true" aria-labelledby="xx-undo-modal-title" @click.self="closeUndo">
        <div class="xx-modal-box">
          <header class="xx-modal-header">
            <span id="xx-undo-modal-title">撤销这条变动？</span>
            <button type="button" class="xx-modal-close" aria-label="关闭撤销确认" @click="closeUndo">×</button>
          </header>
          <section class="xx-modal-section">
            <p class="xx-modal-text">
              撤销后 亲近度：<b class="xx-up" style="font-size:17px">{{ fmtNum(undoPreview.after) }}</b>
              <span class="xx-muted-text">（当前 {{ fmtNum(undoPreview.current) }}，本次 {{ fmtDelta(undoPreview.delta) }}）</span>
            </p>
            <p class="xx-help">将撤销此事件的全部维度，与当前查看筛选无关。</p>
            <p class="xx-modal-text xx-muted-text" style="margin-top:8px;">原因：{{ displayReason(undoPreview.reason) }}</p>
          </section>
          <div class="xx-modal-actions">
            <button type="button" class="xx-btn xx-btn-mini" @click="closeUndo">取消</button>
            <button type="button" class="xx-btn xx-btn-danger" @click="confirmUndo">确认撤销</button>
          </div>
        </div>
      </div>

      <!-- 恢复确认弹窗：恢复前要求确认当前 Bot/空间与备份名 -->
      <div v-if="restorePreview" class="xx-modal" role="dialog" aria-modal="true" aria-labelledby="xx-restore-modal-title" @click.self="closeRestore">
        <div class="xx-modal-box">
          <header class="xx-modal-header">
            <span id="xx-restore-modal-title">确认恢复数据空间？</span>
            <button type="button" class="xx-modal-close" aria-label="关闭恢复确认" @click="closeRestore">×</button>
          </header>
          <section class="xx-modal-section">
            <p class="xx-modal-text">
              将恢复 Bot <b class="xx-mono">{{ restorePreview.snapshot.bot_id }}</b> 的
              <b>{{ restorePreview.snapshot.scope_mode === 'global' ? '全局空间' : '按群空间' }}</b>。
            </p>
            <p class="xx-modal-text xx-muted-text" style="margin-top:6px;">备份文件：<code>{{ restorePreview.backup_name }}</code></p>
            <p class="xx-modal-text xx-muted-text" style="margin-top:6px;">当前空间会被备份后替换，确认后再读取。</p>
          </section>
          <div class="xx-modal-actions">
            <button type="button" class="xx-btn xx-btn-mini" @click="closeRestore">取消</button>
            <button type="button" class="xx-btn xx-btn-danger" @click="confirmRestore">确认恢复</button>
          </div>
        </div>
      </div>
    </div>
  `,
}).mount("#app");
