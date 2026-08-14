import { Button, Input, Picker, Text, Textarea, View } from "@tarojs/components";
import Taro from "@tarojs/taro";
import {
  formatDecimalCurrency,
  manualSourceRoleLabel,
  transactionTypeLabel,
  type EnrichmentPatch,
  type ManualInputRecord,
  type Transaction,
} from "@family-spending/core";
import { useCallback, useEffect, useState } from "react";

import { familySpendingService } from "../../api/client";
import { PageFrame } from "../../components/PageFrame";
import "../transactions/index.css";

const FOLLOW_DEFAULT = "__merchant_default__";

export default function TransactionDetailPage() {
  const transactionId = Taro.getCurrentInstance().router?.params?.id ?? "";
  const [transaction, setTransaction] = useState<Transaction | null>(null);
  const [manualInputs, setManualInputs] = useState<readonly ManualInputRecord[]>([]);
  const [categories, setCategories] = useState<readonly string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!transactionId) { setError("缺少 Transaction ID。"); setLoading(false); return; }
    setLoading(true); setError(null);
    try {
      const [nextTransaction, nextManualInputs, nextCategories] = await Promise.all([
        familySpendingService.getTransaction(transactionId),
        familySpendingService.listManualInputs(),
        familySpendingService.listCategories(),
      ]);
      setTransaction(nextTransaction);
      setManualInputs(nextManualInputs.filter((item) => item.transaction_id === nextTransaction.id));
      setCategories(nextCategories);
    } catch (caught) { setError(caught instanceof Error ? caught.message : String(caught)); }
    finally { setLoading(false); }
  }, [transactionId]);

  useEffect(() => { void load(); }, [load]);

  return (
    <PageFrame title="交易详情" description="查看来源、分类与 Manual Source。" page="/transaction-detail" workspace="transactions">
      {loading && !transaction ? <View className="page-state">正在读取交易…</View> : null}
      {error && !transaction ? <View className="page-state page-state--error"><Text>{error}</Text><Button className="button button--ghost" onClick={() => void load()}>重试</Button></View> : null}
      {transaction ? <TransactionDetail transaction={transaction} manualInputs={manualInputs} categories={categories} onChanged={async (nextId) => { if (nextId !== transactionId) { await Taro.redirectTo({ url: `/pages/transaction-detail/index?id=${encodeURIComponent(nextId)}` }); } else { await load(); } }} /> : null}
    </PageFrame>
  );
}

function TransactionDetail({ transaction, manualInputs, categories, onChanged }: { transaction: Transaction; manualInputs: readonly ManualInputRecord[]; categories: readonly string[]; onChanged: (transactionId: string) => Promise<void> }) {
  const [merchant, setMerchant] = useState(transaction.enrichment.merchant ?? "");
  const initialCategory = transaction.enrichment.category_source === "manual_override" || transaction.enrichment.category_source === "transaction_override" ? transaction.enrichment.category : FOLLOW_DEFAULT;
  const [category, setCategory] = useState(initialCategory);
  const [note, setNote] = useState(transaction.enrichment.note ?? "");
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const selectableCategories = category !== FOLLOW_DEFAULT && !categories.includes(category) ? [...categories, category] : categories;
  const categoryOptions = [FOLLOW_DEFAULT, ...selectableCategories];
  const categoryLabels = [transaction.enrichment.default_category ? `跟随 Merchant 默认（${transaction.enrichment.default_category}）` : "跟随 Merchant 默认（无默认时为待分类）", ...selectableCategories];
  const categoryIndex = Math.max(0, categoryOptions.indexOf(category));

  useEffect(() => {
    setMerchant(transaction.enrichment.merchant ?? "");
    setCategory(transaction.enrichment.category_source === "manual_override" || transaction.enrichment.category_source === "transaction_override" ? transaction.enrichment.category : FOLLOW_DEFAULT);
    setNote(transaction.enrichment.note ?? "");
    setStatus(null);
  }, [transaction]);

  async function saveEnrichment(): Promise<void> {
    const patch: EnrichmentPatch = {};
    if (transaction.type === "expense") {
      const nextMerchant = merchant.trim() || null;
      if (nextMerchant !== transaction.enrichment.merchant) patch.merchant = nextMerchant;
      const currentCategory = transaction.enrichment.category_source === "manual_override" || transaction.enrichment.category_source === "transaction_override" ? transaction.enrichment.category : FOLLOW_DEFAULT;
      if (category !== currentCategory) patch.category = category === FOLLOW_DEFAULT ? null : category;
    }
    const nextNote = note.trim() || null;
    if (nextNote !== transaction.enrichment.note) patch.note = nextNote;
    if (Object.keys(patch).length === 0) { setStatus("没有需要保存的修改。"); return; }
    setSaving(true); setStatus("正在保存并刷新统计…");
    try { await familySpendingService.updateEnrichment(transaction.id, patch); setStatus("已保存。"); await onChanged(transaction.id); }
    catch (caught) { setStatus(`保存失败：${caught instanceof Error ? caught.message : String(caught)}`); }
    finally { setSaving(false); }
  }

  return (
    <View className="transaction-mobile-detail">
      <View className="transaction-mobile-card transaction-mobile-detail">
        <Text className="transaction-mobile-detail__title">{transaction.enrichment.display_name}</Text>
        <Text className="transaction-mobile-detail__meta">{transaction.date} · {transactionTypeLabel(transaction.type)} · {formatDecimalCurrency(transaction.amount, transaction.currency)}</Text>
        <View className="transaction-mobile-facts">
          <View className="transaction-mobile-fact"><Text>当前分类</Text><Text>{transaction.enrichment.category}</Text></View>
          <View className="transaction-mobile-fact"><Text>当前 Source</Text><Text>{transaction.source.type}</Text></View>
          <View className="transaction-mobile-fact"><Text>原始 description</Text><Text>{transaction.source.description ?? "—"}</Text></View>
          <View className="transaction-mobile-fact"><Text>Category source</Text><Text>{transaction.enrichment.category_source}</Text></View>
        </View>
      </View>

      <View className="transaction-mobile-card transaction-mobile-form">
        <Text className="transaction-mobile-section-title">当前 Enrichment</Text>
        {transaction.type === "expense" ? <>
          <View className="transaction-mobile-field"><Text className="transaction-mobile-label">Merchant（单笔例外）</Text><Input className="transaction-mobile-input" value={merchant} onInput={(event) => setMerchant(event.detail.value)} /></View>
          <View className="transaction-mobile-field"><Text className="transaction-mobile-label">Category</Text><Picker mode="selector" range={categoryLabels} value={categoryIndex} onChange={(event) => setCategory(categoryOptions[Number(event.detail.value)] ?? FOLLOW_DEFAULT)}><View className="transaction-mobile-picker">{categoryLabels[categoryIndex]}</View></Picker></View>
        </> : <Text className="transaction-mobile-hint">收入不进入 Merchant Mapping；当前仅允许修改 Note。</Text>}
        <View className="transaction-mobile-field"><Text className="transaction-mobile-label">Note</Text><Textarea className="transaction-mobile-textarea" value={note} onInput={(event) => setNote(event.detail.value)} /></View>
        {status ? <Text className="transaction-mobile-status">{status}</Text> : null}
        <Button className="button button--primary" disabled={saving} onClick={() => void saveEnrichment()}>{saving ? "保存中…" : "保存 Enrichment"}</Button>
      </View>

      <View className="transaction-mobile-card transaction-mobile-detail">
        <Text className="transaction-mobile-section-title">Manual Source</Text>
        <Text className="transaction-mobile-hint">更正会创建新的 Source ID 并重新对账；删除只移除指定 Manual Source。</Text>
        {manualInputs.length === 0 ? <View className="empty-state">这笔交易没有关联 Manual Source。</View> : manualInputs.map((item) => <ManualSourceEditor key={item.source_record_id} item={item} onChanged={onChanged} />)}
      </View>
    </View>
  );
}

function ManualSourceEditor({ item, onChanged }: { item: ManualInputRecord; onChanged: (transactionId: string) => Promise<void> }) {
  const [editing, setEditing] = useState(false);
  const [type, setType] = useState(item.type);
  const [date, setDate] = useState(item.date);
  const [amount, setAmount] = useState(item.amount);
  const [description, setDescription] = useState(item.description ?? "");
  const [note, setNote] = useState(item.transaction.enrichment.note ?? "");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  async function correct(): Promise<void> {
    setBusy(true); setStatus("正在保存 Source 更正…");
    try { const result = await familySpendingService.correctManualInput(item.source_record_id, { type, date, amount, description, note: note.trim() || null }); setEditing(false); setStatus("已更正。"); await onChanged(result.manual_input.transaction.id); }
    catch (caught) { setStatus(`更正失败：${caught instanceof Error ? caught.message : String(caught)}`); }
    finally { setBusy(false); }
  }

  async function remove(): Promise<void> {
    const confirmation = await Taro.showModal({ title: "删除 Manual Source", content: `删除「${item.description ?? item.source_record_id}」？后端会重新对账。`, confirmText: "删除", confirmColor: "#a33d3d" });
    if (!confirmation.confirm) return;
    setBusy(true); setStatus("正在删除并重新对账…");
    try {
      const result = await familySpendingService.deleteManualInput(item.source_record_id);
      if (result.transaction_removed) { await Taro.showToast({ title: "交易已移除", icon: "none" }); await Taro.navigateBack(); return; }
      setEditing(false); setStatus("已删除 Manual Source。"); await onChanged(result.transaction_id);
    } catch (caught) { setStatus(`删除失败：${caught instanceof Error ? caught.message : String(caught)}`); }
    finally { setBusy(false); }
  }

  return (
    <View className="manual-mobile-item">
      <View className="manual-mobile-summary"><View><Text>{item.description ?? "（无 description）"}</Text><Text className="manual-mobile-meta">{manualSourceRoleLabel(item.source_role)} · {item.source_record_id}</Text></View><Button className="button button--ghost button--compact" onClick={() => setEditing(!editing)}>{editing ? "收起" : "更正 / 删除"}</Button></View>
      {editing ? <View className="manual-mobile-editor">
        <View className="transaction-mobile-form-actions"><Button className={`button ${type === "expense" ? "button--primary" : "button--ghost"}`} onClick={() => setType("expense")}>支出</Button><Button className={`button ${type === "income" ? "button--primary" : "button--ghost"}`} onClick={() => setType("income")}>收入</Button></View>
        <View className="transaction-mobile-field"><Text className="transaction-mobile-label">日期</Text><Input className="transaction-mobile-input" value={date} onInput={(event) => setDate(event.detail.value)} /></View>
        <View className="transaction-mobile-field"><Text className="transaction-mobile-label">金额</Text><Input className="transaction-mobile-input" type="digit" value={amount} onInput={(event) => setAmount(event.detail.value)} /></View>
        <View className="transaction-mobile-field"><Text className="transaction-mobile-label">description</Text><Input className="transaction-mobile-input" value={description} onInput={(event) => setDescription(event.detail.value)} /></View>
        <View className="transaction-mobile-field"><Text className="transaction-mobile-label">Note</Text><Textarea className="transaction-mobile-textarea" value={note} onInput={(event) => setNote(event.detail.value)} /></View>
        {status ? <Text className="transaction-mobile-status">{status}</Text> : null}
        <View className="transaction-mobile-form-actions"><Button className="button button--danger" disabled={busy} onClick={() => void remove()}>删除</Button><Button className="button button--primary" disabled={busy} onClick={() => void correct()}>{busy ? "处理中…" : "保存更正"}</Button></View>
      </View> : null}
    </View>
  );
}
