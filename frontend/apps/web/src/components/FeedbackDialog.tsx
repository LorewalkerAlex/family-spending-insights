import type { FeedbackContext } from "@family-spending/core";
import * as Dialog from "@radix-ui/react-dialog";
import { useState, type FormEvent } from "react";

import { familySpendingService } from "../api/client";
import type { WorkspaceId } from "../app/workspaces";
import { Button } from "./ui/Button";

interface FeedbackDialogProps {
  page: string;
  workspace: WorkspaceId | undefined;
  onCreated?: () => void;
}

/** Global product-feedback command; financial-data review remains a separate workspace. */
export function FeedbackDialog({ page, workspace, onCreated }: FeedbackDialogProps) {
  const [open, setOpen] = useState(false);
  const [content, setContent] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const normalized = content.trim();
    if (!normalized) {
      setError("请填写反馈内容。");
      return;
    }

    const context: FeedbackContext = {
      runtime: "desktop_web",
      page,
    };
    if (workspace) {
      context.workspace = workspace;
    }

    setSaving(true);
    setError(null);
    try {
      await familySpendingService.createFeedback({ content: normalized, context });
      setContent("");
      setOpen(false);
      onCreated?.();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Trigger asChild>
        <Button variant="primary">发送反馈</Button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="dialog-content">
          <div className="dialog-heading">
            <div>
              <Dialog.Title className="dialog-title">发送产品反馈</Dialog.Title>
              <Dialog.Description className="dialog-description">
                反馈会保存在本地，并自动记录当前页面上下文。
              </Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <Button variant="ghost" aria-label="关闭反馈窗口">
                ×
              </Button>
            </Dialog.Close>
          </div>
          <form onSubmit={(event) => void submit(event)}>
            <label className="field-label" htmlFor="feedback-content">
              反馈内容
            </label>
            <textarea
              id="feedback-content"
              className="textarea"
              value={content}
              onChange={(event) => setContent(event.target.value)}
              rows={6}
              placeholder="哪里不清楚、哪里不顺手，或者你希望增加什么？"
              autoFocus
            />
            {error ? <p className="form-error">{error}</p> : null}
            <div className="dialog-actions">
              <Dialog.Close asChild>
                <Button disabled={saving}>取消</Button>
              </Dialog.Close>
              <Button variant="primary" type="submit" disabled={saving}>
                {saving ? "提交中…" : "提交反馈"}
              </Button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
