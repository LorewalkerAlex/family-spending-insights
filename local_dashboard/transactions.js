(function startTransactionWorkspace() {
  "use strict";

  const applicationApi = globalThis.FamilySpendingApplicationApi;
  if (!applicationApi) {
    throw new Error("FamilySpendingApplicationApi 未加载。");
  }

  const service = applicationApi.createApplicationService();
  const FOLLOW_DEFAULT_VALUE = "__merchant_default__";
  const state = {
    categories: [],
    transactions: [],
    selectedTransactionId: null,
    loading: false,
  };

  const elements = {
    section: document.querySelector("[data-transactions-workspace]"),
    loading: document.querySelector("[data-transactions-loading]"),
    error: document.querySelector("[data-transactions-error]"),
    errorMessage: document.querySelector("[data-transactions-error-message]"),
    retry: document.querySelector("[data-action='retry-transactions']"),
    empty: document.querySelector("[data-transactions-empty]"),
    count: document.querySelector("[data-transactions-count]"),
    list: document.querySelector("[data-transactions-list]"),
    detailEmpty: document.querySelector("[data-transaction-detail-empty]"),
    detail: document.querySelector("[data-transaction-detail]"),
    detailTitle: document.querySelector("[data-transaction-title]"),
    detailMeta: document.querySelector("[data-transaction-meta]"),
    sourceDescription: document.querySelector("[data-transaction-source-description]"),
    effectiveCategory: document.querySelector("[data-effective-category]"),
    defaultCategory: document.querySelector("[data-default-category]"),
    merchant: document.querySelector("[data-enrichment-merchant]"),
    category: document.querySelector("[data-enrichment-category]"),
    note: document.querySelector("[data-enrichment-note]"),
    form: document.querySelector("[data-enrichment-form]"),
    save: document.querySelector("[data-action='save-enrichment']"),
    reset: document.querySelector("[data-action='reset-enrichment']"),
    formStatus: document.querySelector("[data-enrichment-status]"),
    monthSelect: document.querySelector("[data-month-select]"),
    dashboardReload: document.querySelector("[data-action='reload']"),
  };

  if (!elements.section) {
    return;
  }

  function setHidden(element, hidden) {
    if (element) {
      element.hidden = hidden;
    }
  }

  function selectedMonth() {
    const value = elements.monthSelect ? elements.monthSelect.value : "";
    return /^\d{4}-\d{2}$/.test(value) ? value : null;
  }

  function currentTransaction() {
    return (
      state.transactions.find((item) => item.id === state.selectedTransactionId) || null
    );
  }

  function normalizeText(value) {
    const trimmed = value.trim();
    return trimmed === "" ? null : trimmed;
  }

  function formatAmount(transaction) {
    const amount = Number(transaction.amount);
    if (!Number.isFinite(amount)) {
      return transaction.amount;
    }
    try {
      return new Intl.NumberFormat("zh-CN", {
        style: "currency",
        currency: transaction.currency,
        minimumFractionDigits: 2,
      }).format(amount);
    } catch (_error) {
      return `${transaction.currency} ${transaction.amount}`;
    }
  }

  function transactionLabel(transaction) {
    return transaction.enrichment.displayName || transaction.source.description || transaction.id;
  }

  function visibleTransactions() {
    const month = selectedMonth();
    return state.transactions
      .filter((transaction) => !month || transaction.date.startsWith(`${month}-`))
      .slice()
      .sort(
        (left, right) =>
          right.date.localeCompare(left.date) ||
          transactionLabel(left).localeCompare(transactionLabel(right), "zh-CN") ||
          left.id.localeCompare(right.id),
      );
  }

  function renderWorkspaceState() {
    setHidden(elements.loading, !state.loading);
    setHidden(elements.error, true);
    setHidden(elements.section, false);
  }

  function renderError(error) {
    state.loading = false;
    setHidden(elements.loading, true);
    setHidden(elements.error, false);
    setHidden(elements.empty, true);
    elements.list.replaceChildren();
    elements.count.textContent = "—";
    elements.errorMessage.textContent =
      error instanceof applicationApi.ApplicationApiError
        ? error.message
        : error instanceof Error
          ? error.message
          : String(error);
    renderDetail(null);
  }

  function createTransactionButton(transaction) {
    const item = document.createElement("li");
    item.className = "transaction-list__item";

    const button = document.createElement("button");
    button.type = "button";
    button.className = "transaction-row";
    button.dataset.transactionId = transaction.id;
    button.classList.toggle(
      "transaction-row--selected",
      transaction.id === state.selectedTransactionId,
    );

    const main = document.createElement("span");
    main.className = "transaction-row__main";
    const name = document.createElement("strong");
    name.className = "transaction-row__name";
    name.textContent = transactionLabel(transaction);
    const meta = document.createElement("span");
    meta.className = "transaction-row__meta";
    meta.textContent = `${transaction.date} · ${transaction.source.type}`;
    main.append(name, meta);

    const values = document.createElement("span");
    values.className = "transaction-row__values";
    const amount = document.createElement("strong");
    amount.className = "transaction-row__amount";
    amount.textContent = formatAmount(transaction);
    const category = document.createElement("span");
    category.className = "transaction-row__category";
    category.textContent = transaction.enrichment.category;
    if (transaction.enrichment.isUnclassified) {
      category.classList.add("transaction-row__category--attention");
    }
    values.append(amount, category);

    button.append(main, values);
    button.addEventListener("click", () => {
      state.selectedTransactionId = transaction.id;
      renderList();
      renderDetail(transaction);
    });
    item.append(button);
    return item;
  }

  function renderList() {
    const transactions = visibleTransactions();
    elements.list.replaceChildren();
    transactions.forEach((transaction) => {
      elements.list.append(createTransactionButton(transaction));
    });
    elements.count.textContent = String(transactions.length);
    setHidden(elements.empty, transactions.length !== 0);
    setHidden(elements.list, transactions.length === 0);

    if (
      state.selectedTransactionId &&
      !transactions.some((transaction) => transaction.id === state.selectedTransactionId)
    ) {
      state.selectedTransactionId = null;
      renderDetail(null);
    }
  }

  function categoryControlValue(transaction) {
    return transaction.enrichment.categorySource === "manual_override" ||
      transaction.enrichment.categorySource === "transaction_override"
      ? transaction.enrichment.category
      : FOLLOW_DEFAULT_VALUE;
  }

  function renderCategoryOptions(transaction) {
    elements.category.replaceChildren();
    if (transaction.type === "income") {
      const incomeCategory = document.createElement("option");
      incomeCategory.value = transaction.enrichment.category;
      incomeCategory.textContent = transaction.enrichment.category;
      elements.category.append(incomeCategory);
      elements.category.value = transaction.enrichment.category;
      elements.category.disabled = true;
      return;
    }

    elements.category.disabled = false;
    const followDefault = document.createElement("option");
    followDefault.value = FOLLOW_DEFAULT_VALUE;
    followDefault.textContent = transaction.enrichment.defaultCategory
      ? `跟随商户默认（${transaction.enrichment.defaultCategory}）`
      : "跟随商户默认（无默认时为待分类）";
    elements.category.append(followDefault);
    state.categories.forEach((categoryName) => {
      const option = document.createElement("option");
      option.value = categoryName;
      option.textContent = categoryName;
      elements.category.append(option);
    });
    const controlValue = categoryControlValue(transaction);
    if (
      controlValue !== FOLLOW_DEFAULT_VALUE &&
      !state.categories.includes(controlValue)
    ) {
      const currentCategory = document.createElement("option");
      currentCategory.value = controlValue;
      currentCategory.textContent = controlValue;
      elements.category.append(currentCategory);
    }
    elements.category.value = controlValue;
  }

  function renderDetail(transaction) {
    const hasTransaction = transaction !== null;
    setHidden(elements.detailEmpty, hasTransaction);
    setHidden(elements.detail, !hasTransaction);
    if (!hasTransaction) {
      return;
    }

    const isIncome = transaction.type === "income";
    elements.detailTitle.textContent = transactionLabel(transaction);
    elements.detailMeta.textContent = `${transaction.date} · ${formatAmount(transaction)} · ${transaction.type}`;
    elements.sourceDescription.textContent = transaction.source.description || "—";
    elements.effectiveCategory.textContent = transaction.enrichment.category;
    elements.defaultCategory.textContent = isIncome
      ? "收入不使用 Merchant 默认分类"
      : transaction.enrichment.defaultCategory || "无";
    elements.merchant.value = transaction.enrichment.merchant || "";
    elements.merchant.disabled = isIncome;
    elements.merchant.placeholder = isIncome ? "收入不使用 Merchant Mapping" : "";
    renderCategoryOptions(transaction);
    elements.note.value = transaction.enrichment.note || "";
    elements.formStatus.textContent = isIncome
      ? "收入保留原始 description，不进入 Merchant Mapping；当前只允许修改 Note。"
      : "";
    elements.save.disabled = false;
  }

  function buildPatch(transaction) {
    const patch = {};
    if (transaction.type !== "income") {
      const nextMerchant = normalizeText(elements.merchant.value);
      const currentMerchant = transaction.enrichment.merchant;
      if (nextMerchant !== currentMerchant) {
        patch.merchant = nextMerchant;
      }

      const currentCategoryControl = categoryControlValue(transaction);
      if (elements.category.value !== currentCategoryControl) {
        patch.category =
          elements.category.value === FOLLOW_DEFAULT_VALUE
            ? null
            : elements.category.value;
      }
    }

    const nextNote = normalizeText(elements.note.value);
    const currentNote = transaction.enrichment.note;
    if (nextNote !== currentNote) {
      patch.note = nextNote;
    }
    return patch;
  }

  function replaceTransaction(updated) {
    state.transactions = state.transactions.map((transaction) =>
      transaction.id === updated.id ? updated : transaction,
    );
  }

  async function saveEnrichment(event) {
    event.preventDefault();
    const transaction = currentTransaction();
    if (!transaction) {
      return;
    }
    const patch = buildPatch(transaction);
    if (Object.keys(patch).length === 0) {
      elements.formStatus.textContent = "没有需要保存的修改。";
      return;
    }

    elements.save.disabled = true;
    elements.formStatus.textContent = "正在保存并刷新下游统计…";
    try {
      const updated = await service.updateEnrichment(transaction.id, patch);
      replaceTransaction(updated);
      state.selectedTransactionId = updated.id;
      renderList();
      renderDetail(updated);
      elements.formStatus.textContent = "已保存；下游统计已由后端重新生成。";
      if (elements.dashboardReload) {
        elements.dashboardReload.click();
      }
    } catch (error) {
      elements.formStatus.textContent =
        error instanceof applicationApi.ApplicationApiError
          ? `保存失败：${error.message}`
          : `保存失败：${error instanceof Error ? error.message : String(error)}`;
    } finally {
      elements.save.disabled = false;
    }
  }

  async function loadApplicationData() {
    state.loading = true;
    renderWorkspaceState();
    try {
      const [categories, transactions] = await Promise.all([
        service.getCategories(),
        service.getTransactions(),
      ]);
      state.categories = categories.slice();
      state.transactions = transactions.slice();
      state.loading = false;
      setHidden(elements.loading, true);
      setHidden(elements.error, true);
      renderList();
      const selected = currentTransaction();
      renderDetail(selected);
    } catch (error) {
      renderError(error);
    }
  }

  elements.form.addEventListener("submit", saveEnrichment);
  elements.reset.addEventListener("click", () => renderDetail(currentTransaction()));
  elements.retry.addEventListener("click", loadApplicationData);
  if (elements.monthSelect) {
    elements.monthSelect.addEventListener("change", () => {
      state.selectedTransactionId = null;
      renderList();
      renderDetail(null);
    });
    const observer = new MutationObserver(() => {
      if (!state.loading) {
        renderList();
      }
    });
    observer.observe(elements.monthSelect, { childList: true });
  }

  loadApplicationData();
})();
