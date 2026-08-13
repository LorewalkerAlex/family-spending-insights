import { Button, Text, Textarea, View } from "@tarojs/components";
import { useState, type PropsWithChildren } from "react";

import { familySpendingService, miniRuntime } from "../api/client";
import { miniTheme } from "../theme";

interface PageFrameProps extends PropsWithChildren {
  title: string;
  description: string;
  page: string;
  workspace: string;
  onFeedbackCreated?: () => void;
}

/** Shared Mini page frame keeps Send Feedback available without creating a second navigation system. */
export function PageFrame({
  title,
  description,
  page,
  workspace,
  onFeedbackCreated,
  children,
}: PageFrameProps) {
  const [composerOpen, setComposerOpen] = useState(false);
  const [content, setContent] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submitFeedback(): Promise<void> {
    const trimmed = content.trim();
    if (!trimmed || submitting) {
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await familySpendingService.createFeedback({
        content: trimmed,
        context: {
          runtime: miniRuntime,
          page,
          workspace,
        },
      });
      setContent("");
      setComposerOpen(false);
      onFeedbackCreated?.();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <View
      className={`mini-page ${miniRuntime === "mini_h5" ? "mini-page--h5" : ""}`.trim()}
      style={{
        backgroundColor: miniTheme.colors.canvas,
        color: miniTheme.colors.text,
      }}
    >
      <View className="mini-page__header">
        <View className="mini-page__heading">
          <Text className="mini-page__title">{title}</Text>
          <Text className="mini-page__description">{description}</Text>
        </View>
        <Button className="button button--compact" onClick={() => setComposerOpen(true)}>
          发送反馈
        </Button>
      </View>

      {children}

      {composerOpen ? (
        <View className="feedback-overlay" onClick={() => !submitting && setComposerOpen(false)}>
          <View className="feedback-sheet" onClick={(event) => event.stopPropagation()}>
            <Text className="feedback-sheet__title">发送产品反馈</Text>
            <Text className="feedback-sheet__hint">
              当前页面和运行端会自动记录；交易分类问题请继续使用“审核”。
            </Text>
            <Textarea
              className="feedback-textarea"
              value={content}
              maxlength={2000}
              placeholder="写下你希望调整的产品体验…"
              onInput={(event) => setContent(event.detail.value)}
            />
            {error ? <Text className="inline-error">{error}</Text> : null}
            <View className="feedback-sheet__actions">
              <Button
                className="button button--ghost"
                disabled={submitting}
                onClick={() => setComposerOpen(false)}
              >
                取消
              </Button>
              <Button
                className="button button--primary"
                disabled={!content.trim() || submitting}
                onClick={() => void submitFeedback()}
              >
                {submitting ? "提交中…" : "提交反馈"}
              </Button>
            </View>
          </View>
        </View>
      ) : null}
    </View>
  );
}
