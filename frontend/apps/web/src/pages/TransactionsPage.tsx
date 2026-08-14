import {
  formatDecimalCurrency,
  manualSourceRoleLabel,
  toTransactionListItemViewModel,
  transactionTypeLabel,
  type EnrichmentPatch,
  type ManualInputRecord,
  type Transaction,
} from "@family-spending/core";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";

import { familySpendingService } from "../api/client";
import { Button } from "../components/ui/Button";
import "./transactions.css";

const FOLLOW_DEFAULT = "__merchant_default__";

interface TransactionsPageProps {
  refreshKey: number;
  focusTransactionId: string | null;
}

export function TransactionsPage({ refreshKey, focusTransactionId }: TransactionsPageProps) {
  const [transactions, setTransactions] = useState<readonly Transaction[]>([]);
  const [manualInputs, setManualInputs] = useState<readonly ManualInputRecord[]>([]);
  const [categories, setCategories] = useState<readonly string[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(focusTransactionId);
  const [month, setMonth] = useState("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (preferredId?: string | null) => {
    setLoading(true);
    setError(null);
    try {
      const [nextTransactions, nextManualInputs, nextCategories] = await Promise.all([
        familySpendingService.listTransactions(),
        familySpendingService.listManualInputs(),
        familySpendingService.listCategories(),
      ]);
      setTransactions(nextTransactions);
      setManualInputs(nextManualInputs);
      setCategories(nextCategories);
      const wanted = preferredId ?? selectedId;
      setSelectedId(wanted && nextTransactions.some((item) => item.id === wanted) ? wanted : null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, [selectedId]);

  useEffect(() => { void load(focusTransactionId); }, [refreshKey, focusTransactionId]);

  const months = useMemo(
    () => Array.from(new Set(transactions.map((item) => item.date.slice(0, 7)))).sort().reverse(),
    [transactions],
  );
  const visible = useMemo(
    () => transactions
      .filter((item) => month === "all" || item.date.startsWith(`${month}-`))
      .slice()
      .sort((left, right) => right.date.localeCompare(left.date) || left.id.localeCompare(right.id)),
    [transactions, month],
  );
  const selected = transactions.find((item) => item.id === selectedId) ?? null;
  const selectedManualInputs = selected
    ? manualInputs.filter((item) => item.transaction_id === selected.id)
    : [];

  return (
    <div className="transactions-workspace">
      <section className="transaction-list-pane">
        <div className="transaction-toolbar">
          <div><strong>{visible.length}</strong><span> 笔交易</span></div>
          <select className="field-control transaction-month" value={month} onChange={(event) => { setMonth(event.target.value); setSelectedId(null); }}>
            <option value="all">全部月份</option>
            {months.map((item) => <option value={item} key={item}>{item}</option>)}
          </select>
        </div>
        {loading && transactions.length === 0 ? <div className="page-state">正在读取交易…</div> : null}
        {error && transactions.length === 0 ? (
          <div className="page-state page-state--error"><p>{error}</p><Button onClick={() => void load()}>重试</Button></div>
        ) : null}
        {!loading && !error && visible.length === 0 ? <div className="empty-state">当前范围没有交易。</div> : null}
        <div className="transaction-list">
          {visible.map((transaction) => {
            const item = toTransactionListItemViewModel(transaction);
            return (
              <button key={item.id} type="button" className={`transaction-row${selectedId === item.id ? " transaction-row--active" : ""}`} onClick={() => setSelectedId(item.id)}>
                <span className="transaction-row__main"><strong>{item.displayName}</strong><small>{item.date} · {item.typeLabel} · {item.sourceLabel}</small></span>
                <span className="transaction-row__value"><strong>{item.amountText}</strong><small className={item.isUnclassified ? "transaction-row__attention" : ""}>{item.category}</small></span>
              </button>
            );
          })}
        </div>
      </section>

      <section className="transaction-detail-pane">
        {selected ? (
          <TransactionDetail
            transaction={selected}
            manualInputs={selectedManualInputs}
            categories={categories}
            onChanged={(preferredId) => void load(preferredId)}
          />
        ) : (
          <div className="transaction-detail-empty"><strong>选择一笔交易</strong><p>查看来源、当前分类和 transaction-only 例外；Manual Source 也在这里更正或删除。</p></div>
        )}
      </section>
    </div>
  );
}

interface TransactionDetailProps {
  transaction: Transaction;
  manualInputs: readonly ManualInputRecord[];
  categories: readonly string[];
  onChanged: (transactionId: string | null) => void;
}

function TransactionDetail({ transaction, manualInputs, categories, onChanged }: TransactionDetailProps) {
  const [merchant, setMerchant] = useState(transaction.enrichment.merchant ?? "");
  const [category, setCategory] = useState(
    transaction.enrichment.category_source === "manual_override" || transaction.enrichment.category_source === "transaction_override"
      ? transaction.enrichment.category
      : FOLLOW_DEFAULT,
  );
  const [note, setNote] = useState(transaction.enrichment.note ?? "");
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [sourceToEdit, setSourceToEdit] = useState<string | null>(null);

  useEffect(() => {
    setMerchant(transaction.enrichment.merchant ?? "");
    setCategory(
      transaction.enrichment.category_source === "manual_override" || transaction.enrichment.category_source === "transaction_override"
        ? transaction.enrichment.category
        : FOLLOW_DEFAULT,
    );
    setNote(transaction.enrichment.note ?? "");
    setStatus(null);
    setSourceToEdit(null);
  }, [transaction.id, transaction.enrichment]);

  async function saveEnrichment(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const patch: EnrichmentPatch = {};
    if (transaction.type === "expense") {
      const nextMerchant = merchant.trim() || null;
      if (nextMerchant !== transaction.enrichment.merchant) patch.merchant = nextMerchant;
      const currentCategoryControl =
        transaction.enrichment.category_source === "manual_override" || transaction.enrichment.category_source === "transaction_override"
          ? transaction.enrichment.category
          : FOLLOW_DEFAULT;
      if (category !== currentCategoryControl) patch.category = category === FOLLOW_DEFAULT ? null : category;
    }
    const nextNote = note.trim() || null;
    if (nextNote !== transaction.enrichment.note) patch.note = nextNote;
    if (Object.keys(patch).length === 0) { setStatus("没有需要保存的修改。"); return; }

    setSaving(true); setStatus("正在保存并刷新下游统计…");
    try {
      const updated = await familySpendingService.updateEnrichment(transaction.id, patch);
      setStatus("已保存；后端已刷新下游统计。");
      onChanged(updated.id);
    } catch (caught) { setStatus(`保存失败：${caught instanceof Error ? caught.message : String(caught)}`); }
    finally { setSaving(false); }
  }

  return (
    <div className="transaction-detail">
      <header className="transaction-detail__header">
        <div><p className="eyebrow">{transactionTypeLabel(transaction.type)}</p><h2>{transaction.enrichment.display_name}</h2><p>{transaction.date} · {formatDecimalCurrency(transaction.amount, transaction.currency)}</p></div>
        <span className={`status-pill${transaction.enrichment.is_unclassified ? " transaction-status--attention" : ""}`}>{transaction.enrichment.category}</span>
      </header>

      <dl className="transaction-facts">
        <div><dt>当前 Source</dt><dd>{transaction.source.type}</dd></div>
        <div><dt>原始 description</dt><dd>{transaction.source.description ?? "—"}</dd></div>
        <div><dt>Merchant 默认分类</dt><dd>{transaction.type === "income" ? "收入不使用 Merchant Mapping" : transaction.enrichment.default_category ?? "无"}</dd></div>
        <div><dt>Category source</dt><dd>{transaction.enrichment.category_source}</dd></div>
      </dl>

      <form className="transaction-detail__form" onSubmit={(event) => void saveEnrichment(event)}>
        <h3>当前 Enrichment</h3>
        {transaction.type === "expense" ? (
          <>
            <label><span className="field-label">Merchant（单笔例外）</span><input className="field-control" value={merchant} onChange={(event) => setMerchant(event.target.value)} /></label>
            <label><span className="field-label">Category</span><select className="field-control" value={category} onChange={(event) => setCategory(event.target.value)}><option value={FOLLOW_DEFAULT}>跟随 Merchant 默认{transaction.enrichment.default_category ? `（${transaction.enrichment.default_category}）` : "（无默认时为待分类）"}</option>{categories.map((item) => <option key={item} value={item}>{item}</option>)}{category !== FOLLOW_DEFAULT && !categories.includes(category) ? <option value={category}>{category}</option> : null}</select></label>
          </>
        ) : <p className="transaction-note">收入不进入 Merchant Mapping；当前仅允许修改 Note。</p>}
        <label><span className="field-label">Note</span><textarea className="textarea transaction-detail__note" rows={3} value={note} onChange={(event) => setNote(event.target.value)} /></label>
        {status ? <p className="form-status">{status}</p> : null}
        <div className="transaction-form-actions"><Button type="button" onClick={() => { setMerchant(transaction.enrichment.merchant ?? ""); setNote(transaction.enrichment.note ?? ""); setCategory(transaction.enrichment.category_source === "manual_override" || transaction.enrichment.category_source === "transaction_override" ? transaction.enrichment.category : FOLLOW_DEFAULT); }}>还原表单</Button><Button type="submit" variant="primary" disabled={saving}>{saving ? "保存中…" : "保存 Enrichment"}</Button></div>
      </form>

      <section className="manual-source-section">
        <div className="section-heading"><div><h3>Manual Source</h3><p>更正会创建新的 Source ID 并重新 Reconciliation；删除只移除这条 Manual Source。</p></div></div>
        {manualInputs.length === 0 ? <div className="empty-state">这笔交易没有关联 Manual Source。</div> : manualInputs.map((item) => (
          <div className="manual-source-card" key={item.source_record_id}>
            <div className="manual-source-card__summary"><div><strong>{item.description ?? "（无 description）"}</strong><small>{manualSourceRoleLabel(item.source_role)} · {item.source_record_id}</small></div><Button variant="ghost" onClick={() => setSourceToEdit(sourceToEdit === item.source_record_id ? null : item.source_record_id)}>{sourceToEdit === item.source_record_id ? "收起" : "更正 / 删除"}</Button></div>
            {sourceToEdit === item.source_record_id ? <ManualSourceEditor item={item} onChanged={onChanged} /> : null}
          </div>
        ))}
      </section>
    </div>
  );
}

function ManualSourceEditor({ item, onChanged }: { item: ManualInputRecord; onChanged: (transactionId: string | null) => void }) {
  const [type, setType] = useState(item.type);
  const [date, setDate] = useState(item.date);
  const [amount, setAmount] = useState(item.amount);
  const [description, setDescription] = useState(item.description ?? "");
  const [note, setNote] = useState(item.transaction.enrichment.note ?? "");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  async function correct(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault(); setBusy(true); setStatus("正在以新 Source ID 保存更正…");
    try {
      const result = await familySpendingService.correctManualInput(item.source_record_id, { type, date, amount, description, note: note.trim() || null });
      setStatus(`已替换 ${result.replaced_source_record_id}。`);
      onChanged(result.manual_input.transaction.id);
    } catch (caught) { setStatus(`更正失败：${caught instanceof Error ? caught.message : String(caught)}`); }
    finally { setBusy(false); }
  }

  async function remove(): Promise<void> {
    if (!window.confirm(`删除 Manual Source「${item.description ?? item.source_record_id}」？`)) return;
    setBusy(true); setStatus("正在删除 Manual Source 并重新对账…");
    try {
      const result = await familySpendingService.deleteManualInput(item.source_record_id);
      onChanged(result.transaction_removed ? null : result.transaction_id);
    } catch (caught) { setStatus(`删除失败：${caught instanceof Error ? caught.message : String(caught)}`); }
    finally { setBusy(false); }
  }

  return (
    <form className="manual-source-editor" onSubmit={(event) => void correct(event)}>
      <div className="transaction-form__grid">
        <label><span className="field-label">类型</span><select className="field-control" value={type} onChange={(event) => setType(event.target.value as "income" | "expense")}><option value="expense">支出</option><option value="income">收入</option></select></label>
        <label><span className="field-label">日期</span><input className="field-control" type="date" value={date} onChange={(event) => setDate(event.target.value)} /></label>
        <label><span className="field-label">金额</span><input className="field-control" value={amount} onChange={(event) => setAmount(event.target.value)} /></label>
      </div>
      <label><span className="field-label">description</span><input className="field-control" value={description} onChange={(event) => setDescription(event.target.value)} /></label>
      <label><span className="field-label">Note</span><textarea className="textarea transaction-detail__note" rows={2} value={note} onChange={(event) => setNote(event.target.value)} /></label>
      {status ? <p className="form-status">{status}</p> : null}
      <div className="transaction-form-actions transaction-form-actions--spread"><Button type="button" className="transaction-delete" disabled={busy} onClick={() => void remove()}>删除 Manual Source</Button><Button type="submit" variant="primary" disabled={busy}>{busy ? "处理中…" : "保存 Source 更正"}</Button></div>
    </form>
  );
}
