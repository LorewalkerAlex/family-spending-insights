import { Button, Input, Text, Textarea, View } from "@tarojs/components";
import Taro from "@tarojs/taro";
import { findSimilarManualDescriptions, manualInputActionLabel, normalizeManualDescription } from "@family-spending/core";
import { useEffect, useMemo, useState } from "react";

import { familySpendingService } from "../../api/client";
import { PageFrame } from "../../components/PageFrame";
import "../transactions/index.css";

function todayIsoDate(): string {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
}

export default function AddTransactionPage() {
  const [type, setType] = useState<"expense" | "income">("expense");
  const [date, setDate] = useState(todayIsoDate());
  const [amount, setAmount] = useState("");
  const [description, setDescription] = useState("");
  const [note, setNote] = useState("");
  const [descriptions, setDescriptions] = useState<readonly string[]>([]);
  const [confirmedNewDescription, setConfirmedNewDescription] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { void familySpendingService.listManualDescriptions().then(setDescriptions).catch((caught) => setError(caught instanceof Error ? caught.message : String(caught))); }, []);
  const suggestions = useMemo(() => findSimilarManualDescriptions(description, descriptions), [description, descriptions]);
  const duplicate = useMemo(() => {
    const normalized = normalizeManualDescription(description);
    return normalized ? descriptions.find((item) => normalizeManualDescription(item) === normalized) ?? null : null;
  }, [description, descriptions]);

  async function submit(): Promise<void> {
    const trimmed = description.trim();
    if (!date || !amount.trim() || !trimmed) { setError("请填写日期、金额和 description。"); return; }
    if (duplicate && duplicate !== trimmed && confirmedNewDescription !== trimmed) { setError(`发现规范化后相同的历史 description「${duplicate}」。请复用，或明确按当前文本新建。`); return; }
    setSaving(true); setError(null); setStatus("正在录入、对账并刷新统计…");
    try {
      const result = await familySpendingService.createManualInput({ type, date, amount, description: trimmed, note: note.trim() || null });
      setStatus(manualInputActionLabel(result.action));
      await Taro.showToast({ title: "已添加", icon: "success" });
      await Taro.navigateBack();
    } catch (caught) { setStatus(null); setError(caught instanceof Error ? caught.message : String(caught)); }
    finally { setSaving(false); }
  }

  return (
    <PageFrame title="添加交易" description="创建 Manual Source，由后端统一对账。" page="/add-transaction" workspace="transactions">
      <View className="transaction-mobile-card transaction-mobile-form">
        <View className="transaction-mobile-field"><Text className="transaction-mobile-label">类型</Text><View className="transaction-mobile-form-actions"><Button className={`button ${type === "expense" ? "button--primary" : "button--ghost"}`} onClick={() => setType("expense")}>支出</Button><Button className={`button ${type === "income" ? "button--primary" : "button--ghost"}`} onClick={() => setType("income")}>收入</Button></View></View>
        <View className="transaction-mobile-field"><Text className="transaction-mobile-label">日期</Text><Input className="transaction-mobile-input" type="text" value={date} onInput={(event) => setDate(event.detail.value)} placeholder="YYYY-MM-DD" /></View>
        <View className="transaction-mobile-field"><Text className="transaction-mobile-label">金额</Text><Input className="transaction-mobile-input" type="digit" value={amount} onInput={(event) => setAmount(event.detail.value)} placeholder="88.50" /></View>
        <View className="transaction-mobile-field"><Text className="transaction-mobile-label">原始 description</Text><Input className="transaction-mobile-input" value={description} onInput={(event) => { setDescription(event.detail.value); setConfirmedNewDescription(null); setError(null); }} placeholder="例如：小区门口早餐摊" /></View>
        {suggestions.length > 0 ? <View className="transaction-mobile-suggestions">{suggestions.map((item) => <Button key={item} className="transaction-mobile-suggestion" onClick={() => { setDescription(item); setConfirmedNewDescription(null); setError(null); }}>使用已有：{item}</Button>)}{duplicate && duplicate !== description.trim() ? <Button className="transaction-mobile-suggestion" onClick={() => { setConfirmedNewDescription(description.trim()); setError(null); }}>仍按当前文本新建</Button> : null}</View> : null}
        <View className="transaction-mobile-field"><Text className="transaction-mobile-label">备注（可选）</Text><Textarea className="transaction-mobile-textarea" value={note} onInput={(event) => setNote(event.detail.value)} /></View>
        {status ? <Text className="transaction-mobile-status">{status}</Text> : null}
        {error ? <Text className="transaction-mobile-error">{error}</Text> : null}
        <Button className="button button--primary" disabled={saving} onClick={() => void submit()}>{saving ? "保存中…" : "添加交易"}</Button>
      </View>
    </PageFrame>
  );
}
