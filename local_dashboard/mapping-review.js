(function startMappingReview() {
  "use strict";

  const applicationApi = globalThis.FamilySpendingApplicationApi;
  const section = document.querySelector("[data-mapping-review]");
  if (!applicationApi || !section) {
    return;
  }

  const service = applicationApi.createApplicationService();
  const state = {
    workspace: null,
    selectedDescription: null,
    preview: null,
    loading: false,
  };
  const elements = {
    loading: section.querySelector("[data-mapping-review-loading]"),
    error: section.querySelector("[data-mapping-review-error]"),
    errorMessage: section.querySelector("[data-mapping-review-error-message]"),
    retry: section.querySelector("[data-action='retry-mapping-review']"),
    empty: section.querySelector("[data-mapping-review-empty]"),
    count: section.querySelector("[data-mapping-review-count]"),
    list: section.querySelector("[data-mapping-review-list]"),
    detailEmpty: section.querySelector("[data-mapping-review-detail-empty]"),
    detail: section.querySelector("[data-mapping-review-detail]"),
    description: section.querySelector("[data-mapping-review-description]"),
    meta: section.querySelector("[data-mapping-review-meta]"),
    exceptionHint: section.querySelector("[data-mapping-review-exception-hint]"),
    form: section.querySelector("[data-mapping-review-form]"),
    merchant: section.querySelector("[data-mapping-review-merchant]"),
    merchantHint: section.querySelector("[data-mapping-review-merchant-hint]"),
    merchantSuggestions: section.querySelector("[data-mapping-review-merchant-suggestions]"),
    category: section.querySelector("[data-mapping-review-category]"),
    previewButton: section.querySelector("[data-action='preview-mapping-review']"),
    applyButton: section.querySelector("[data-action='apply-mapping-review']"),
    impact: section.querySelector("[data-mapping-review-impact]"),
    impactList: section.querySelector("[data-mapping-review-impact-list]"),
    status: section.querySelector("[data-mapping-review-status]"),
    singleException: section.querySelector("[data-action='mapping-review-single-exception']"),
    transactionWorkspace: document.querySelector("[data-transactions-workspace]"),
    transactionsReload: document.querySelector("[data-action='retry-transactions']"),
    dashboardReload: document.querySelector("[data-action='reload']"),
  };

  function setHidden(element, hidden) {
    if (element) {
      element.hidden = hidden;
    }
  }

  function currentItem() {
    if (!state.workspace || !state.selectedDescription) {
      return null;
    }
    return (
      state.workspace.items.find(
        (item) => item.description === state.selectedDescription,
      ) || null
    );
  }

  function currentMerchantOption() {
    if (!state.workspace) {
      return null;
    }
    const name = elements.merchant.value.trim();
    return state.workspace.merchants.find((merchant) => merchant.name === name) || null;
  }

  function formatAmount(item) {
    const amount = Number(item.totalAmount);
    if (!Number.isFinite(amount)) {
      return `${item.currency} ${item.totalAmount}`;
    }
    try {
      return new Intl.NumberFormat("zh-CN", {
        style: "currency",
        currency: item.currency,
        minimumFractionDigits: 2,
      }).format(amount);
    } catch (_error) {
      return `${item.currency} ${item.totalAmount}`;
    }
  }

  function renderError(error) {
    state.loading = false;
    setHidden(elements.loading, true);
    setHidden(elements.error, false);
    elements.errorMessage.textContent =
      error instanceof applicationApi.ApplicationApiError
        ? error.message
        : error instanceof Error
          ? error.message
          : String(error);
  }

  function invalidatePreview() {
    state.preview = null;
    setHidden(elements.impact, true);
    elements.impactList.replaceChildren();
    elements.applyButton.disabled = true;
    elements.status.textContent = "";
  }

  function selectDescription(description) {
    state.selectedDescription = description;
    invalidatePreview();
    renderList();
    renderDetail();
  }

  function createReviewButton(item) {
    const listItem = document.createElement("li");
    listItem.className = "mapping-review-list__item";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "mapping-review-row";
    button.classList.toggle(
      "mapping-review-row--selected",
      item.description === state.selectedDescription,
    );

    const identity = document.createElement("span");
    identity.className = "mapping-review-row__identity";
    const name = document.createElement("strong");
    name.textContent = item.description;
    const meta = document.createElement("span");
    meta.textContent = `${item.latestDate} · ${item.sourceTypes.join(" + ")}`;
    identity.append(name, meta);

    const values = document.createElement("span");
    values.className = "mapping-review-row__values";
    const amount = document.createElement("strong");
    amount.textContent = formatAmount(item);
    const count = document.createElement("span");
    count.textContent = `${item.transactionCount} 笔`;
    values.append(amount, count);

    button.append(identity, values);
    button.addEventListener("click", () => selectDescription(item.description));
    listItem.append(button);
    return listItem;
  }

  function renderList() {
    if (!state.workspace) {
      return;
    }
    elements.list.replaceChildren();
    state.workspace.items.forEach((item) => {
      elements.list.append(createReviewButton(item));
    });
    elements.count.textContent = String(state.workspace.items.length);
    const isEmpty = state.workspace.items.length === 0;
    setHidden(elements.empty, !isEmpty);
    setHidden(elements.list, isEmpty);
    if (
      state.selectedDescription &&
      !state.workspace.items.some(
        (item) => item.description === state.selectedDescription,
      )
    ) {
      state.selectedDescription = null;
    }
  }

  function renderCategoryOptions() {
    elements.category.replaceChildren();
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "请选择默认 Category";
    elements.category.append(placeholder);
    state.workspace.categories.forEach((category) => {
      const option = document.createElement("option");
      option.value = category;
      option.textContent = category;
      elements.category.append(option);
    });
  }

  function appendMerchantSuggestion(name) {
    const option = state.workspace.merchants.find((merchant) => merchant.name === name);
    if (!option) {
      return;
    }
    const button = document.createElement("button");
    button.type = "button";
    button.className = "mapping-review-suggestion";
    button.textContent = `使用已有：${option.name} · ${option.defaultCategory}`;
    button.addEventListener("click", () => {
      elements.merchant.value = option.name;
      elements.category.value = option.defaultCategory;
      invalidatePreview();
      renderMerchantHints();
    });
    elements.merchantSuggestions.append(button);
  }

  function renderMerchantHints() {
    if (!state.workspace) {
      return;
    }
    elements.merchantSuggestions.replaceChildren();
    const query = elements.merchant.value.trim();
    if (query === "") {
      elements.merchantHint.textContent = "先搜索并复用已有 Merchant；确实不存在时才新建。";
      setHidden(elements.merchantSuggestions, true);
      return;
    }
    const existing = currentMerchantOption();
    if (existing) {
      elements.merchantHint.textContent = `已有 Merchant；当前默认 Category：${existing.defaultCategory}。修改 Category 会影响所有仍跟随该 Merchant 默认值的交易。`;
      if (elements.category.value === "") {
        elements.category.value = existing.defaultCategory;
      }
    } else {
      elements.merchantHint.textContent = "当前名称将创建新 Merchant；Apply 前还会再次确认。";
    }
    const suggestions = applicationApi.findSimilarMerchantNames(
      query,
      state.workspace.merchants,
    );
    suggestions
      .filter((name) => name !== query)
      .forEach(appendMerchantSuggestion);
    setHidden(elements.merchantSuggestions, elements.merchantSuggestions.children.length === 0);
  }

  function renderDetail() {
    const item = currentItem();
    setHidden(elements.detailEmpty, item !== null);
    setHidden(elements.detail, item === null);
    if (!item) {
      return;
    }
    elements.description.textContent = item.description;
    elements.meta.textContent = `${item.transactionCount} 笔 · 原始金额合计 ${formatAmount(item)} · 最近 ${item.latestDate}`;
    elements.exceptionHint.textContent =
      item.transactionOnlyExceptionCount > 0
        ? `其中 ${item.transactionOnlyExceptionCount} 笔已有单笔 Merchant 例外；Mapping Apply 不会覆盖这些 Merchant。`
        : "当前组没有单笔 Merchant 例外。";
    elements.merchant.value = "";
    renderCategoryOptions();
    renderMerchantHints();
  }

  function addImpactLine(text, emphasis = false) {
    const item = document.createElement("li");
    if (emphasis) {
      item.className = "mapping-review-impact__emphasis";
    }
    item.textContent = text;
    elements.impactList.append(item);
  }

  function renderPreview(preview) {
    elements.impactList.replaceChildren();
    addImpactLine(
      `description → Merchant：${preview.description} → ${preview.merchant}；将更新 ${preview.descriptionAffectedTransactionCount} 笔仍跟随 Mapping 的交易。`,
      true,
    );
    if (preview.isNewMerchant) {
      addImpactLine(`将新建 Merchant「${preview.merchant}」，默认 Category 为「${preview.category}」。`);
    } else if (preview.previousDefaultCategory !== preview.category) {
      addImpactLine(
        `Merchant 默认 Category：${preview.previousDefaultCategory} → ${preview.category}；另外更新 ${preview.defaultCategoryAffectedTransactionCount} 笔当前 Merchant state。`,
        true,
      );
    } else {
      addImpactLine(`Merchant「${preview.merchant}」继续使用默认 Category「${preview.category}」。`);
    }
    if (preview.preservedMerchantExceptionCount > 0) {
      addImpactLine(`保留 ${preview.preservedMerchantExceptionCount} 笔 transaction-only Merchant 例外。`);
    }
    if (preview.preservedCategoryExceptionCount > 0) {
      addImpactLine(`保留 ${preview.preservedCategoryExceptionCount} 笔显式 Category 例外。`);
    }
    addImpactLine(`本次预计修改 ${preview.totalAffectedTransactionCount} 笔 Enrichment state。`);
    setHidden(elements.impact, false);
    elements.applyButton.disabled = false;
  }

  async function previewMapping(event) {
    event.preventDefault();
    const item = currentItem();
    if (!item) {
      return;
    }
    const merchant = elements.merchant.value.trim();
    const category = elements.category.value;
    if (merchant === "" || category === "") {
      elements.status.textContent = "请选择 Merchant 和默认 Category 后再预览。";
      return;
    }
    elements.previewButton.disabled = true;
    elements.applyButton.disabled = true;
    elements.status.textContent = "正在计算 Mapping 影响范围…";
    try {
      const preview = await service.previewMappingReview({
        description: item.description,
        merchant,
        category,
      });
      state.preview = preview;
      renderPreview(preview);
      elements.status.textContent = "预览已锁定；若输入变化，需要重新预览。";
    } catch (error) {
      invalidatePreview();
      elements.status.textContent =
        error instanceof applicationApi.ApplicationApiError
          ? `预览失败：${error.message}`
          : `预览失败：${error instanceof Error ? error.message : String(error)}`;
    } finally {
      elements.previewButton.disabled = false;
    }
  }

  async function applyMapping() {
    const preview = state.preview;
    if (!preview) {
      elements.status.textContent = "请先预览影响范围。";
      return;
    }
    let confirmNewMerchant = false;
    if (preview.isNewMerchant) {
      confirmNewMerchant = globalThis.confirm(
        `将创建新 Merchant「${preview.merchant}」，并把 description「${preview.description}」映射到它。确认继续？`,
      );
      if (!confirmNewMerchant) {
        elements.status.textContent = "已取消新 Merchant 创建。";
        return;
      }
    }
    elements.previewButton.disabled = true;
    elements.applyButton.disabled = true;
    elements.status.textContent = "正在写入 Mapping、传播 Enrichment 并刷新统计…";
    try {
      await service.applyMappingReview({
        description: preview.description,
        merchant: preview.merchant,
        category: preview.category,
        previewToken: preview.token,
        confirmNewMerchant,
      });
      elements.status.textContent = "Mapping 已应用；正在刷新审核队列、Transaction 与统计。";
      state.preview = null;
      if (elements.transactionsReload) {
        elements.transactionsReload.click();
      } else {
        await loadWorkspace();
      }
      if (elements.dashboardReload) {
        elements.dashboardReload.click();
      }
    } catch (error) {
      elements.status.textContent =
        error instanceof applicationApi.ApplicationApiError
          ? `应用失败：${error.message}`
          : `应用失败：${error instanceof Error ? error.message : String(error)}`;
      elements.applyButton.disabled = false;
    } finally {
      elements.previewButton.disabled = false;
    }
  }

  async function loadWorkspace() {
    state.loading = true;
    setHidden(elements.loading, false);
    setHidden(elements.error, true);
    try {
      const workspace = await service.getMappingReviews();
      state.workspace = workspace;
      state.loading = false;
      setHidden(elements.loading, true);
      renderList();
      if (!state.selectedDescription && workspace.items.length > 0) {
        state.selectedDescription = workspace.items[0].description;
      }
      renderList();
      renderDetail();
    } catch (error) {
      renderError(error);
    }
  }

  elements.form.addEventListener("submit", previewMapping);
  elements.applyButton.addEventListener("click", applyMapping);
  elements.retry.addEventListener("click", loadWorkspace);
  elements.merchant.addEventListener("input", () => {
    invalidatePreview();
    renderMerchantHints();
  });
  elements.category.addEventListener("change", invalidatePreview);
  elements.singleException.addEventListener("click", () => {
    if (elements.transactionWorkspace) {
      elements.transactionWorkspace.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  });
  if (elements.transactionsReload) {
    elements.transactionsReload.addEventListener("click", () => {
      loadWorkspace();
    });
  }

  loadWorkspace();
})();