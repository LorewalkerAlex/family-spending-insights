(function startManualEntry() {
  "use strict";

  const applicationApi = globalThis.FamilySpendingApplicationApi;
  const form = document.querySelector("[data-manual-entry-form]");
  if (!applicationApi || !form) {
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
  };

  let manualDescriptions = [];
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

  async function loadManualDescriptions() {
    elements.submit.disabled = true;
    elements.status.textContent = "正在读取历史 Manual description…";
    try {
      manualDescriptions = [...(await service.getManualDescriptions())];
      elements.status.textContent = "";
      elements.submit.disabled = false;
    } catch (error) {
      elements.status.textContent =
        error instanceof applicationApi.ApplicationApiError
          ? `无法加载录入表单：${error.message}`
          : `无法加载录入表单：${error instanceof Error ? error.message : String(error)}`;
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
      const actionLabel = {
        created: "已创建新 Transaction",
        matched: "已匹配已有 Transaction",
        reused: "已复用既有 Source Link",
      }[result.action];
      rememberDescription(description);
      resetForm();
      elements.status.textContent = `${actionLabel}：${result.transaction.enrichment.displayName}。后端统计已刷新。`;
      if (elements.transactionsReload) {
        elements.transactionsReload.click();
      }
      if (elements.dashboardReload) {
        elements.dashboardReload.click();
      }
    } catch (error) {
      elements.status.textContent =
        error instanceof applicationApi.ApplicationApiError
          ? `录入失败：${error.message}`
          : `录入失败：${error instanceof Error ? error.message : String(error)}`;
    } finally {
      elements.submit.disabled = false;
    }
  }

  resetForm();
  elements.description.addEventListener("input", () => {
    confirmedNewDescription = null;
    renderDescriptionSuggestions();
  });
  elements.description.addEventListener("focus", renderDescriptionSuggestions);
  form.addEventListener("submit", submitManualInput);
  loadManualDescriptions();
})();
