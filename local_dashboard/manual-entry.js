(function startManualEntry() {
  "use strict";

  const applicationApi = globalThis.FamilySpendingApplicationApi;
  const form = document.querySelector("[data-manual-entry-form]");
  const management = document.querySelector("[data-manual-input-management]");
  if (!applicationApi || !form || !management) {
    return;
  }

  const service = applicationApi.createApplicationService();
  const elements = {
    type: form.querySelector("[data-manual-type]"),
    date: form.querySelector("[data-manual-date]"),
    amount: form.querySelector("[data-manual-amount]"),
    description: form.querySelector("[data-manual-description]"),
    suggestions: form.querySelector("[data-manual-description-suggestions]"),
    note: form.querySelector("[data-manual-note]"),
    submit: form.querySelector("[data-action='submit-manual-input']"),
    status: form.querySelector("[data-manual-status]"),
    dashboardReload: document.querySelector("[data-action='reload']"),
    transactionsReload: document.querySelector("[data-action='retry-transactions']"),
    mappingReload: document.querySelector("[data-action='retry-mapping-review']"),
    managementCount: management.querySelector("[data-manual-input-count]"),
    managementLoading: management.querySelector("[data-manual-input-loading]"),
    managementError: management.querySelector("[data-manual-input-error]"),
    managementErrorMessage: management.querySelector("[data-manual-input-error-message]"),
    managementEmpty: management.querySelector("[data-manual-input-empty]"),
    managementList: management.querySelector("[data-manual-input-list]"),
    managementRetry: management.querySelector("[data-action='retry-manual-inputs']"),
    detailEmpty: management.querySelector("[data-manual-input-detail-empty]"),
    detail: management.querySelector("[data-manual-input-detail]"),
    detailTitle: management.querySelector("[data-manual-input-detail-title]"),
    detailMeta: management.querySelector("[data-manual-input-detail-meta]"),
    correctionForm: management.querySelector("[data-manual-correction-form]"),
    editType: management.querySelector("[data-manual-edit-type]"),
    editDate: management.querySelector("[data-manual-edit-date]"),
    editAmount: management.querySelector("[data-manual-edit-amount]"),
    editDescription: management.querySelector("[data-manual-edit-description]"),
    editNote: management.querySelector("[data-manual-edit-note]"),
    correctionSubmit: management.querySelector("[data-action='correct-manual-input']"),
    deleteButton: management.querySelector("[data-action='delete-manual-input']"),
    managementStatus: management.querySelector("[data-manual-input-status]"),
  };

  let manualDescriptions = [];
  let manualInputs = [];
  let selectedSourceRecordId = null;
  let confirmedNewDescription = null;

  function todayIsoDate() {
    const now = new Date();
    const local = new Date(now.getTime() - now.getTimezoneOffset() * 60 * 1000);
    return local.toISOString().slice(0, 10);
  }

  function normalizeText(value) {
    const trimmed = value.trim();
    return trimmed === "" ? null : trimmed;
  }

  function actionLabel(action) {
    return {
      created: "已创建新 Transaction",
      matched: "已匹配已有 Transaction",
      reused: "已保留既有 Transaction identity",
    }[action] || action;
  }

  function resetForm() {
    form.reset();
    elements.type.value = "expense";
    elements.date.value = todayIsoDate();
    confirmedNewDescription = null;
    renderDescriptionSuggestions();
  }

  function rememberDescription(description) {
    if (!manualDescriptions.includes(description)) {
      manualDescriptions = [description, ...manualDescriptions];
    }
  }

  function currentDescription() {
    return elements.description.value.trim();
  }

  function normalizedDuplicate(description) {
    const normalized = applicationApi.normalizeManualDescription(description);
    if (normalized === "") {
      return null;
    }
    return (
      manualDescriptions.find(
        (candidate) =>
          applicationApi.normalizeManualDescription(candidate) === normalized,
      ) || null
    );
  }

  function useExistingDescription(description) {
    elements.description.value = description;
    confirmedNewDescription = null;
    elements.status.textContent = `将复用已有 Manual description：${description}`;
    renderDescriptionSuggestions();
  }

  function confirmNewDescription(description) {
    confirmedNewDescription = description;
    elements.status.textContent = `已确认按当前文本新建 description：${description}`;
    renderDescriptionSuggestions();
  }

  function appendSuggestionButton(label, onClick, modifier = "") {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `manual-description-suggestion${modifier}`;
    button.textContent = label;
    button.addEventListener("click", onClick);
    elements.suggestions.append(button);
  }

  function renderDescriptionSuggestions() {
    elements.suggestions.replaceChildren();
    const description = currentDescription();
    if (description === "") {
      elements.suggestions.hidden = true;
      return;
    }

    const matches = applicationApi.findSimilarManualDescriptions(
      description,
      manualDescriptions,
    );
    if (matches.length === 0) {
      elements.suggestions.hidden = true;
      return;
    }

    matches.forEach((candidate) => {
      appendSuggestionButton(`使用已有：${candidate}`, () => {
        useExistingDescription(candidate);
      });
    });

    const duplicate = normalizedDuplicate(description);
    if (
      duplicate !== null &&
      duplicate !== description &&
      confirmedNewDescription !== description
    ) {
      appendSuggestionButton(
        `仍按当前文本新建：${description}`,
        () => {
          confirmNewDescription(description);
        },
        " manual-description-suggestion--new",
      );
    }
    elements.suggestions.hidden = false;
  }

  function selectedManualInput() {
    return (
      manualInputs.find((item) => item.sourceRecordId === selectedSourceRecordId) || null
    );
  }

  function renderManualInputList() {
    elements.managementList.replaceChildren();
    elements.managementCount.textContent = String(manualInputs.length);
    elements.managementEmpty.hidden = manualInputs.length !== 0;

    manualInputs.forEach((item) => {
      const row = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "manual-input-list__button";
      if (item.sourceRecordId === selectedSourceRecordId) {
        button.classList.add("manual-input-list__button--active");
      }

      const title = document.createElement("strong");
      title.textContent = item.description || "（无 description）";
      const meta = document.createElement("span");
      const typeLabel = item.type === "income" ? "收入" : "支出";
      const roleLabel = item.sourceRole === "authoritative" ? "权威来源" : "支持来源";
      meta.textContent = `${item.date} · ${typeLabel} · ${item.currency} ${item.amount} · ${roleLabel}`;
      button.append(title, meta);
      button.addEventListener("click", () => {
        selectedSourceRecordId = item.sourceRecordId;
        renderManualInputList();
        renderManualInputDetail();
      });
      row.append(button);
      elements.managementList.append(row);
    });
  }

  function renderManualInputDetail() {
    const item = selectedManualInput();
    elements.detailEmpty.hidden = item !== null;
    elements.detail.hidden = item === null;
    if (item === null) {
      return;
    }

    elements.detailTitle.textContent = item.description || "（无 description）";
    const roleLabel = item.sourceRole === "authoritative" ? "Manual 为当前权威来源" : "Manual 已作为支持来源关联";
    elements.detailMeta.textContent =
      `${roleLabel} · Source ${item.sourceRecordId} · Transaction ${item.transactionId}`;
    elements.editType.value = item.type;
    elements.editDate.value = item.date;
    elements.editAmount.value = item.amount;
    elements.editDescription.value = item.description || "";
    elements.editNote.value = item.transaction.enrichment.note || "";
  }

  function renderManagementState({ loading = false, error = null } = {}) {
    elements.managementLoading.hidden = !loading;
    elements.managementError.hidden = error === null;
    if (error !== null) {
      elements.managementErrorMessage.textContent = error;
    }
  }

  async function loadManualDescriptions() {
    manualDescriptions = [...(await service.getManualDescriptions())];
    renderDescriptionSuggestions();
  }

  async function loadManualInputs(preferredSourceRecordId = selectedSourceRecordId) {
    renderManagementState({ loading: true });
    try {
      manualInputs = [...(await service.getManualInputs())];
      selectedSourceRecordId = manualInputs.some(
        (item) => item.sourceRecordId === preferredSourceRecordId,
      )
        ? preferredSourceRecordId
        : null;
      renderManagementState();
      renderManualInputList();
      renderManualInputDetail();
    } catch (error) {
      const message =
        error instanceof applicationApi.ApplicationApiError
          ? error.message
          : error instanceof Error
            ? error.message
            : String(error);
      renderManagementState({ error: `无法加载 Manual Inputs：${message}` });
    }
  }

  async function loadManualData() {
    elements.submit.disabled = true;
    elements.status.textContent = "正在读取 Manual Source…";
    try {
      await Promise.all([loadManualDescriptions(), loadManualInputs()]);
      elements.status.textContent = "";
      elements.submit.disabled = false;
    } catch (error) {
      elements.status.textContent =
        error instanceof applicationApi.ApplicationApiError
          ? `无法加载录入表单：${error.message}`
          : `无法加载录入表单：${error instanceof Error ? error.message : String(error)}`;
    }
  }

  function refreshDownstream() {
    if (elements.transactionsReload) {
      elements.transactionsReload.click();
    }
    if (elements.mappingReload) {
      elements.mappingReload.click();
    }
    if (elements.dashboardReload) {
      elements.dashboardReload.click();
    }
  }

  async function submitManualInput(event) {
    event.preventDefault();
    const description = currentDescription();
    const duplicate = normalizedDuplicate(description);
    if (
      duplicate !== null &&
      duplicate !== description &&
      confirmedNewDescription !== description
    ) {
      elements.status.textContent =
        `发现规范化后相同的历史 description「${duplicate}」。` +
        "请选择复用，或明确确认仍按当前文本新建。";
      renderDescriptionSuggestions();
      return;
    }

    elements.submit.disabled = true;
    elements.status.textContent = "正在录入、对账并刷新统计…";
    try {
      const result = await service.createManualInput({
        type: elements.type.value,
        date: elements.date.value,
        amount: elements.amount.value.trim(),
        description,
        note: normalizeText(elements.note.value),
      });
      rememberDescription(description);
      resetForm();
      selectedSourceRecordId = result.sourceRecordId;
      await loadManualInputs(result.sourceRecordId);
      elements.status.textContent =
        `${actionLabel(result.action)}：${result.transaction.enrichment.displayName}。后端统计已刷新。`;
      refreshDownstream();
    } catch (error) {
      elements.status.textContent =
        error instanceof applicationApi.ApplicationApiError
          ? `录入失败：${error.message}`
          : `录入失败：${error instanceof Error ? error.message : String(error)}`;
    } finally {
      elements.submit.disabled = false;
    }
  }

  async function correctManualInput(event) {
    event.preventDefault();
    const item = selectedManualInput();
    if (item === null) {
      return;
    }

    elements.correctionSubmit.disabled = true;
    elements.deleteButton.disabled = true;
    elements.managementStatus.textContent = "正在以新 Source ID 保存更正并重新对账…";
    try {
      const result = await service.correctManualInput(item.sourceRecordId, {
        type: elements.editType.value,
        date: elements.editDate.value,
        amount: elements.editAmount.value.trim(),
        description: elements.editDescription.value.trim(),
        note: normalizeText(elements.editNote.value),
      });
      await loadManualDescriptions();
      selectedSourceRecordId = result.manualInput.sourceRecordId;
      await loadManualInputs(result.manualInput.sourceRecordId);
      elements.managementStatus.textContent =
        `已用新 Source ${result.manualInput.sourceRecordId} 替换 ${result.replacedSourceRecordId}；` +
        `${actionLabel(result.manualInput.action)}。`;
      refreshDownstream();
    } catch (error) {
      elements.managementStatus.textContent =
        error instanceof applicationApi.ApplicationApiError
          ? `更正失败：${error.message}`
          : `更正失败：${error instanceof Error ? error.message : String(error)}`;
    } finally {
      elements.correctionSubmit.disabled = false;
      elements.deleteButton.disabled = false;
    }
  }

  async function deleteManualInput() {
    const item = selectedManualInput();
    if (item === null) {
      return;
    }
    const confirmed = globalThis.confirm(
      `删除 Manual Input「${item.description || item.sourceRecordId}」？\n` +
        "后端会重新对账；只有没有其他来源支撑时，对应 Transaction 才会一并消失。",
    );
    if (!confirmed) {
      return;
    }

    elements.correctionSubmit.disabled = true;
    elements.deleteButton.disabled = true;
    elements.managementStatus.textContent = "正在删除 Manual Source 并重新对账…";
    try {
      const result = await service.deleteManualInput(item.sourceRecordId);
      selectedSourceRecordId = null;
      await loadManualDescriptions();
      await loadManualInputs();
      elements.managementStatus.textContent = result.transactionRemoved
        ? `已删除 ${result.sourceRecordId}；原 Transaction ${result.transactionId} 已无其他来源支撑并移除。`
        : `已删除 ${result.sourceRecordId}；Transaction ${result.transactionId} 仍由其他来源保留。`;
      refreshDownstream();
    } catch (error) {
      elements.managementStatus.textContent =
        error instanceof applicationApi.ApplicationApiError
          ? `删除失败：${error.message}`
          : `删除失败：${error instanceof Error ? error.message : String(error)}`;
    } finally {
      elements.correctionSubmit.disabled = false;
      elements.deleteButton.disabled = false;
    }
  }

  resetForm();
  elements.description.addEventListener("input", () => {
    confirmedNewDescription = null;
    renderDescriptionSuggestions();
  });
  elements.description.addEventListener("focus", renderDescriptionSuggestions);
  form.addEventListener("submit", submitManualInput);
  elements.correctionForm.addEventListener("submit", correctManualInput);
  elements.deleteButton.addEventListener("click", deleteManualInput);
  elements.managementRetry.addEventListener("click", () => loadManualInputs());
  loadManualData();
})();
