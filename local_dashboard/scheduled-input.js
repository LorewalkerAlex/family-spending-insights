(function startScheduledInput() {
  "use strict";

  const api = globalThis.FamilySpendingApplicationApi;
  const root = document.querySelector("[data-scheduled-input]");
  if (!api || !root) {
    return;
  }

  const service = api.createApplicationService();
  const elements = {
    createForm: root.querySelector("[data-scheduled-create-form]"),
    createType: root.querySelector("[data-scheduled-create-type]"),
    createDate: root.querySelector("[data-scheduled-create-date]"),
    createAmount: root.querySelector("[data-scheduled-create-amount]"),
    createDescription: root.querySelector("[data-scheduled-create-description]"),
    createNote: root.querySelector("[data-scheduled-create-note]"),
    createEnabled: root.querySelector("[data-scheduled-create-enabled]"),
    createSubmit: root.querySelector("[data-action='create-scheduled-input']"),
    count: root.querySelector("[data-scheduled-input-count]"),
    loading: root.querySelector("[data-scheduled-input-loading]"),
    error: root.querySelector("[data-scheduled-input-error]"),
    errorMessage: root.querySelector("[data-scheduled-input-error-message]"),
    empty: root.querySelector("[data-scheduled-input-empty]"),
    list: root.querySelector("[data-scheduled-input-list]"),
    detailEmpty: root.querySelector("[data-scheduled-input-detail-empty]"),
    detail: root.querySelector("[data-scheduled-input-detail]"),
    detailTitle: root.querySelector("[data-scheduled-input-detail-title]"),
    detailMeta: root.querySelector("[data-scheduled-input-detail-meta]"),
    editForm: root.querySelector("[data-scheduled-edit-form]"),
    editType: root.querySelector("[data-scheduled-edit-type]"),
    editDate: root.querySelector("[data-scheduled-edit-date]"),
    editAmount: root.querySelector("[data-scheduled-edit-amount]"),
    editDescription: root.querySelector("[data-scheduled-edit-description]"),
    editNote: root.querySelector("[data-scheduled-edit-note]"),
    editEnabled: root.querySelector("[data-scheduled-edit-enabled]"),
    editSubmit: root.querySelector("[data-action='update-scheduled-input']"),
    deleteButton: root.querySelector("[data-action='delete-scheduled-input']"),
    retryButton: root.querySelector("[data-action='retry-scheduled-inputs']"),
    runDueButton: root.querySelector("[data-action='run-scheduled-inputs']"),
    status: root.querySelector("[data-scheduled-input-status]"),
    mappingReload: document.querySelector("[data-action='retry-mapping-review']"),
    transactionsReload: document.querySelector("[data-action='retry-transactions']"),
    dashboardReload: document.querySelector("[data-action='reload']"),
  };

  let rules = [];
  let selectedRuleId = null;

  function todayIsoDate() {
    const now = new Date();
    const local = new Date(now.getTime() - now.getTimezoneOffset() * 60 * 1000);
    const iso = local.toISOString().slice(0, 10);
    if (Number(iso.slice(8, 10)) <= 28) {
      return iso;
    }
    const nextMonth = new Date(local.getFullYear(), local.getMonth() + 1, 1);
    const nextLocal = new Date(
      nextMonth.getTime() - nextMonth.getTimezoneOffset() * 60 * 1000,
    );
    return nextLocal.toISOString().slice(0, 10);
  }

  function normalizeText(value) {
    const trimmed = value.trim();
    return trimmed === "" ? null : trimmed;
  }

  function selectedRule() {
    return rules.find((rule) => rule.id === selectedRuleId) || null;
  }

  function actionLabel(action) {
    return {
      created: "创建 Transaction",
      matched: "匹配既有 Transaction",
      reused: "复用既有 Transaction",
      recovered: "恢复已生成 occurrence",
    }[action] || action;
  }

  function formatLastRun(rule) {
    if (!rule.lastOccurrenceDate) {
      return "尚未执行";
    }
    return `${rule.lastOccurrenceDate} · ${actionLabel(rule.lastAction)} · Transaction ${rule.lastTransactionId}`;
  }

  function setBusy(busy) {
    elements.createSubmit.disabled = busy;
    elements.editSubmit.disabled = busy;
    elements.deleteButton.disabled = busy;
    elements.retryButton.disabled = busy;
    elements.runDueButton.disabled = busy;
  }

  function renderList() {
    elements.list.replaceChildren();
    elements.count.textContent = String(rules.length);
    elements.empty.hidden = rules.length !== 0;
    rules.forEach((rule) => {
      const row = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "scheduled-input-list__button";
      if (rule.id === selectedRuleId) {
        button.classList.add("scheduled-input-list__button--active");
      }
      const title = document.createElement("strong");
      title.textContent = rule.description;
      const meta = document.createElement("span");
      const typeLabel = rule.type === "income" ? "收入" : "支出";
      const stateLabel = rule.enabled ? "启用" : "暂停";
      meta.textContent = `${stateLabel} · 下次 ${rule.nextDate} · ${typeLabel} · ${rule.currency} ${rule.amount}`;
      button.append(title, meta);
      button.addEventListener("click", () => {
        selectedRuleId = rule.id;
        renderList();
        renderDetail();
      });
      row.append(button);
      elements.list.append(row);
    });
  }

  function renderDetail() {
    const rule = selectedRule();
    elements.detailEmpty.hidden = rule !== null;
    elements.detail.hidden = rule === null;
    if (!rule) {
      return;
    }
    elements.detailTitle.textContent = rule.description;
    elements.detailMeta.textContent = `Rule ${rule.id} · ${formatLastRun(rule)}`;
    elements.editType.value = rule.type;
    elements.editDate.value = rule.nextDate;
    elements.editAmount.value = rule.amount;
    elements.editDescription.value = rule.description;
    elements.editNote.value = rule.note || "";
    elements.editEnabled.checked = rule.enabled;
  }

  function renderState({ loading = false, error = null } = {}) {
    elements.loading.hidden = !loading;
    elements.error.hidden = error === null;
    if (error !== null) {
      elements.errorMessage.textContent = error;
    }
  }

  async function loadRules(preferredRuleId = selectedRuleId) {
    renderState({ loading: true });
    try {
      rules = [...(await service.getScheduledInputs())];
      selectedRuleId = rules.some((rule) => rule.id === preferredRuleId)
        ? preferredRuleId
        : null;
      renderState();
      renderList();
      renderDetail();
    } catch (error) {
      const message =
        error instanceof api.ApplicationApiError
          ? error.message
          : error instanceof Error
            ? error.message
            : String(error);
      renderState({ error: `无法加载 Scheduled Inputs：${message}` });
    }
  }

  function commandFrom(prefix) {
    const isCreate = prefix === "create";
    return {
      type: isCreate ? elements.createType.value : elements.editType.value,
      amount: (isCreate ? elements.createAmount.value : elements.editAmount.value).trim(),
      description: (isCreate
        ? elements.createDescription.value
        : elements.editDescription.value
      ).trim(),
      note: normalizeText(isCreate ? elements.createNote.value : elements.editNote.value),
      nextDate: isCreate ? elements.createDate.value : elements.editDate.value,
      enabled: isCreate ? elements.createEnabled.checked : elements.editEnabled.checked,
    };
  }

  function refreshDownstream() {
    globalThis.dispatchEvent(new CustomEvent("family-spending:manual-source-changed"));
    for (const button of [
      elements.mappingReload,
      elements.transactionsReload,
      elements.dashboardReload,
    ]) {
      if (button) {
        button.click();
      }
    }
  }

  async function createRule(event) {
    event.preventDefault();
    setBusy(true);
    elements.status.textContent = "正在保存规则并执行已经到期的 occurrence…";
    try {
      const rule = await service.createScheduledInput(commandFrom("create"));
      elements.createForm.reset();
      elements.createType.value = "expense";
      elements.createDate.value = todayIsoDate();
      elements.createEnabled.checked = true;
      selectedRuleId = rule.id;
      await loadRules(rule.id);
      elements.status.textContent = rule.lastOccurrenceDate
        ? `规则已保存；已执行 ${rule.lastOccurrenceDate} occurrence，下一次 ${rule.nextDate}。`
        : `规则已保存；下一次 ${rule.nextDate}。`;
      if (rule.lastOccurrenceDate) {
        refreshDownstream();
      }
    } catch (error) {
      elements.status.textContent =
        error instanceof api.ApplicationApiError
          ? `创建失败：${error.message}`
          : `创建失败：${error instanceof Error ? error.message : String(error)}`;
    } finally {
      setBusy(false);
    }
  }

  async function updateRule(event) {
    event.preventDefault();
    const rule = selectedRule();
    if (!rule) {
      return;
    }
    setBusy(true);
    elements.status.textContent = "正在更新未来规则并执行已经到期的 occurrence…";
    try {
      const updated = await service.updateScheduledInput(rule.id, commandFrom("edit"));
      await loadRules(updated.id);
      elements.status.textContent = updated.lastOccurrenceDate
        ? `规则已更新；最近 occurrence ${updated.lastOccurrenceDate}，下一次 ${updated.nextDate}。`
        : `规则已更新；下一次 ${updated.nextDate}。`;
      refreshDownstream();
    } catch (error) {
      elements.status.textContent =
        error instanceof api.ApplicationApiError
          ? `更新失败：${error.message}`
          : `更新失败：${error instanceof Error ? error.message : String(error)}`;
    } finally {
      setBusy(false);
    }
  }

  async function deleteRule() {
    const rule = selectedRule();
    if (!rule) {
      return;
    }
    const confirmed = globalThis.confirm(
      `删除 Scheduled Rule「${rule.description}」？\n` +
        "已经生成的 Manual Source / Transaction 不会被删除。",
    );
    if (!confirmed) {
      return;
    }
    setBusy(true);
    elements.status.textContent = "正在删除未来规则…";
    try {
      await service.deleteScheduledInput(rule.id);
      selectedRuleId = null;
      await loadRules();
      elements.status.textContent = "规则已删除；历史 occurrence 保持不变。";
    } catch (error) {
      elements.status.textContent =
        error instanceof api.ApplicationApiError
          ? `删除失败：${error.message}`
          : `删除失败：${error instanceof Error ? error.message : String(error)}`;
    } finally {
      setBusy(false);
    }
  }

  async function runDue() {
    setBusy(true);
    elements.status.textContent = "正在执行所有已到期 Scheduled occurrence…";
    try {
      const result = await service.runDueScheduledInputs();
      await loadRules(selectedRuleId);
      if (result.generatedCount === 0) {
        elements.status.textContent = "当前没有到期 occurrence。";
      } else {
        elements.status.textContent = `已处理 ${result.generatedCount} 个 occurrence；下游统计已刷新。`;
        refreshDownstream();
      }
    } catch (error) {
      elements.status.textContent =
        error instanceof api.ApplicationApiError
          ? `执行失败：${error.message}`
          : `执行失败：${error instanceof Error ? error.message : String(error)}`;
    } finally {
      setBusy(false);
    }
  }

  elements.createDate.value = todayIsoDate();
  elements.createEnabled.checked = true;
  elements.createForm.addEventListener("submit", createRule);
  elements.editForm.addEventListener("submit", updateRule);
  elements.deleteButton.addEventListener("click", deleteRule);
  elements.retryButton.addEventListener("click", () => loadRules());
  elements.runDueButton.addEventListener("click", runDue);
  loadRules();
})();
