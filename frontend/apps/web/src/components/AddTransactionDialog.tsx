import {
  findSimilarManualDescriptions,
  manualInputActionLabel,
  normalizeManualDescription,
} from "@family-spending/core";
import * as Dialog from "@radix-ui/react-dialog";
import { useEffect, useMemo, useState, type FormEvent } from "react";

import { familySpendingService } from "../api/client";
import { Button } from "./ui/Button";

interface AddTransactionDialogProps {
  onCreated: (transactionId: string) => void;
}

function todayIsoDate(): string {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
}

export function AddTransactionDialog({ onCreated }: AddTransactionDialogProps) {
  const [open, setOpen] = useState(false);
  const [type, setType] = useState<"expense" | "income">("expense");
  const [date, setDate] = useState(todayIsoDate());
  const [amount, setAmount] = useState("");
  const [description, setDescription] = useState("");
  const [note, setNote] = useState("");
  const [descriptions, setDescriptions] = useState<readonly string[]>([]);
  const [confirmedNewDescription, setConfirmedNewDescription] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    void familySpendingService
      .listManualDescriptions()
      .then(setDescriptions)
      .catch((caught) => setError(caught instanceof Error ? caught.message : String(caught)));
  }, [open]);

  const suggestions = useMemo(
    () => findSimilarManualDescriptions(description, descriptions),
    [description, descriptions],
  );
  const normalizedDuplicate = useMemo(() => {
    const normalized = normalizeManualDescription(description);
    if (!normalized) return null;
    return descriptions.find((item) => normalizeManualDescription(item) === normalized) ?? null;
  }, [description, descriptions]);

  function reset(): void {
    setType("expense");
    setDate(todayIsoDate());
    setAmount("");
    setDescription("");
    setNote("");
    setConfirmedNewDescription(null);
    setError(null);
    setStatus(null);
  }

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const trimmedDescription = description.trim();
    if (
      normalizedDuplicate &&
      normalizedDuplicate !== trimmedDescription &&
      confirmedNewDescription !== trimmedDescription
    ) {
      setError(
        `发现规范化后相同的历史 description「${normalizedDuplicate}」。请选择复用，或明确按当前文本新建。`,
      );
      return;
    }

    setSaving(true);
    setError(null);
    setStatus("正在录入、对账并刷新统计…");
    try {
      const result = await familySpendingService.createManualInput({
        type,
        date,
        amount,
        description: trimmedDescription,
        note: note.trim() || null,
      });
      setStatus(`${manualInputActionLabel(result.action)}。`);
      reset();
      setOpen(false);
      onCreated(result.transaction.id);
    } catch (caught) {
      setStatus(null);
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next && !saving) reset();
      }}
    >
      <Dialog.Trigger asChild>
        <Button>添加交易</Button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="dialog-content add-transaction-dialog">
          <div className="dialog-heading">
            <div>
              <Dialog.Title className="dialog-title">添加交易</Dialog.Title>
              <Dialog.Description className="dialog-description">
                创建 Manual Source，并由后端完成 Reconciliation、Enrichment 与统计刷新。
              </Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <Button variant="ghost" aria-label="关闭添加交易窗口">×</Button>
            </Dialog.Close>
          </div>

          <form className="transaction-form" onSubmit={(event) => void submit(event)}>
            <div className="transaction-form__grid">
              <label>
                <span className="field-label">类型</span>
                <select className="field-control" value={type} onChange={(event) => setType(event.target.value as "expense" | "income") }>
                  <option value="expense">支出</option>
                  <option value="income">收入</option>
                </select>
              </label>
              <label>
                <span className="field-label">日期</span>
                <input className="field-control" type="date" required value={date} onChange={(event) => setDate(event.target.value)} />
              </label>
              <label>
                <span className="field-label">金额</span>
                <input className="field-control" inputMode="decimal" required value={amount} onChange={(event) => setAmount(event.target.value)} placeholder="88.50" />
              </label>
            </div>

            <label>
              <span className="field-label">原始 description</span>
              <input
                className="field-control"
                required
                value={description}
                onChange={(event) => {
                  setDescription(event.target.value);
                  setConfirmedNewDescription(null);
                  setError(null);
                }}
                placeholder="例如：小区门口早餐摊"
              />
            </label>
            {suggestions.length > 0 ? (
              <div className="description-suggestions" aria-label="历史 description 候选">
                {suggestions.map((item) => (
                  <button key={item} type="button" onClick={() => { setDescription(item); setConfirmedNewDescription(null); setError(null); }}>
                    使用已有：{item}
                  </button>
                ))}
                {normalizedDuplicate && normalizedDuplicate !== description.trim() ? (
                  <button type="button" onClick={() => { setConfirmedNewDescription(description.trim()); setError(null); }}>
                    仍按当前文本新建
                  </button>
                ) : null}
              </div>
            ) : null}

            <label>
              <span className="field-label">备注（可选）</span>
              <textarea className="textarea transaction-form__note" rows={3} value={note} onChange={(event) => setNote(event.target.value)} />
            </label>

            {status ? <p className="form-status">{status}</p> : null}
            {error ? <p className="form-error">{error}</p> : null}
            <div className="dialog-actions">
              <Dialog.Close asChild><Button disabled={saving}>取消</Button></Dialog.Close>
              <Button variant="primary" type="submit" disabled={saving}>{saving ? "保存中…" : "添加交易"}</Button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
