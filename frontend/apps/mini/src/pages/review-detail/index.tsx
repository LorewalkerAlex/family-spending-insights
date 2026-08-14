import { Button, Input, Picker, Text, View } from "@tarojs/components";
import Taro from "@tarojs/taro";
import {
  findSimilarMerchantNames,
  mappingReviewImpactLines,
  toMappingReviewListItemViewModel,
  type MappingReviewItem,
  type MappingReviewPreview,
  type MappingReviewWorkspace,
} from "@family-spending/core";
import { useCallback, useEffect, useMemo, useState } from "react";

import { familySpendingService } from "../../api/client";
import { PageFrame } from "../../components/PageFrame";
import "../review/index.css";

export default function ReviewDetailPage() {
  const description = Taro.getCurrentInstance().router?.params?.description ?? "";
  const [workspace, setWorkspace] = useState<MappingReviewWorkspace | null>(null);
  const [item, setItem] = useState<MappingReviewItem | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!description) {
      setError("缺少待审核 description。");
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const next = await familySpendingService.getMappingReviewWorkspace();
      setWorkspace(next);
      const current = next.items.find((candidate) => candidate.description === description) ?? null;
      setItem(current);
      if (!current) setError("该 description 已不在审核队列中，请返回列表刷新。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, [description]);

  useEffect(() => { void load(); }, [load]);

  return (
    <PageFrame
      title="审核详情"
      description="确认 Merchant Mapping 并预览影响范围。"
      page="/review-detail"
      workspace="review"
    >
      {loading && !item ? <View className="page-state">正在读取审核详情…</View> : null}
      {error && !item ? (
        <View className="page-state page-state--error">
          <Text>{error}</Text>
          <Button className="button button--ghost" onClick={() => void load()}>重试</Button>
        </View>
      ) : null}
      {item && workspace ? <ReviewEditor item={item} workspace={workspace} /> : null}
    </PageFrame>
  );
}

function ReviewEditor({
  item,
  workspace,
}: {
  item: MappingReviewItem;
  workspace: MappingReviewWorkspace;
}) {
  const [merchant, setMerchant] = useState("");
  const [category, setCategory] = useState("");
  const [preview, setPreview] = useState<MappingReviewPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const exactMerchant = workspace.merchants.find((option) => option.name === merchant.trim()) ?? null;
  const suggestions = useMemo(
    () => findSimilarMerchantNames(merchant, workspace.merchants).filter((name) => name !== merchant.trim()),
    [merchant, workspace.merchants],
  );
  const categoryIndex = Math.max(0, workspace.categories.indexOf(category));
  const row = toMappingReviewListItemViewModel(item);
  const impact = preview ? mappingReviewImpactLines(preview) : [];

  function invalidatePreview(): void {
    setPreview(null);
    setStatus(null);
    setError(null);
  }

  function changeMerchant(value: string): void {
    setMerchant(value);
    if (!category) {
      const existing = workspace.merchants.find((option) => option.name === value.trim());
      if (existing) setCategory(existing.default_category);
    }
    invalidatePreview();
  }

  function chooseMerchant(name: string): void {
    const option = workspace.merchants.find((candidate) => candidate.name === name);
    setMerchant(name);
    if (option) setCategory(option.default_category);
    invalidatePreview();
  }

  async function previewMapping(): Promise<void> {
    const nextMerchant = merchant.trim();
    if (!nextMerchant || !category) {
      setError("请选择 Merchant 和默认 Category 后再预览。");
      return;
    }
    setBusy(true);
    setError(null);
    setStatus("正在计算 Mapping 影响范围…");
    try {
      const result = await familySpendingService.previewMappingReview({
        description: item.description,
        merchant: nextMerchant,
        category,
      });
      setPreview(result);
      setStatus("预览已锁定；修改输入后需要重新预览。");
    } catch (caught) {
      setPreview(null);
      setStatus(null);
      setError(`预览失败：${caught instanceof Error ? caught.message : String(caught)}`);
    } finally {
      setBusy(false);
    }
  }

  async function applyMapping(): Promise<void> {
    if (!preview) {
      setError("请先预览影响范围。");
      return;
    }

    let confirmNewMerchant = false;
    if (preview.is_new_merchant) {
      const confirmation = await Taro.showModal({
        title: "创建新 Merchant",
        content: `将创建「${preview.merchant}」，并把当前 description 映射到它。确认继续？`,
        confirmText: "确认应用",
        cancelText: "取消",
      });
      if (!confirmation.confirm) {
        setStatus("已取消新 Merchant 创建。");
        return;
      }
      confirmNewMerchant = true;
    }

    setBusy(true);
    setError(null);
    setStatus("正在应用 Mapping 并刷新统计…");
    try {
      await familySpendingService.applyMappingReview({
        description: preview.description,
        merchant: preview.merchant,
        category: preview.category,
        previewToken: preview.token,
        confirmNewMerchant,
      });
      await Taro.showToast({ title: "审核已应用", icon: "success" });
      await Taro.navigateBack();
    } catch (caught) {
      setError(`应用失败：${caught instanceof Error ? caught.message : String(caught)}`);
      setStatus(null);
      setBusy(false);
    }
  }

  return (
    <View className="review-mobile-detail">
      <View className="review-mobile-card">
        <Text className="review-mobile-detail__title">{item.description}</Text>
        <Text className="review-mobile-detail__meta">
          {row.transactionCountText} · 原始金额合计 {row.amountText} · 最近 {row.latestDate} · {row.sourceTypesText}
        </Text>
        <View className="review-mobile-exception">
          <Text>
            {item.transaction_only_exception_count > 0
              ? `其中 ${item.transaction_only_exception_count} 笔已有 transaction-only Merchant 例外；Apply 不会覆盖。`
              : "当前组没有 transaction-only Merchant 例外。"}
          </Text>
          <Text>单笔 Merchant / Category 例外继续在“交易”中处理。</Text>
        </View>
      </View>

      <View className="review-mobile-card">
        <View className="review-mobile-field">
          <Text className="review-mobile-label">Merchant</Text>
          <Input
            className="review-mobile-input"
            value={merchant}
            placeholder="搜索已有 Merchant，或输入新 Merchant"
            onInput={(event) => changeMerchant(event.detail.value)}
          />
          <Text className="review-mobile-hint">
            {!merchant.trim()
              ? "优先复用已有 Merchant；确实不存在时才新建。"
              : exactMerchant
                ? `已有 Merchant；当前默认 Category：${exactMerchant.default_category}。`
                : "当前名称会作为新 Merchant；Apply 前还会再次确认。"}
          </Text>
        </View>

        {suggestions.length > 0 ? (
          <View className="review-mobile-suggestions">
            {suggestions.map((name) => {
              const option = workspace.merchants.find((candidate) => candidate.name === name);
              return (
                <Button key={name} className="review-mobile-suggestion" onClick={() => chooseMerchant(name)}>
                  使用已有：{name}{option ? ` · ${option.default_category}` : ""}
                </Button>
              );
            })}
          </View>
        ) : null}

        <View className="review-mobile-field">
          <Text className="review-mobile-label">默认 Category</Text>
          <Picker
            mode="selector"
            range={workspace.categories}
            value={categoryIndex}
            onChange={(event) => {
              setCategory(workspace.categories[Number(event.detail.value)] ?? "");
              invalidatePreview();
            }}
          >
            <View className="review-mobile-picker">{category || "请选择默认 Category"}</View>
          </Picker>
        </View>

        {status ? <Text className="review-mobile-status">{status}</Text> : null}
        {error ? <Text className="review-mobile-error">{error}</Text> : null}

        <View className="review-mobile-actions">
          <Button className="button button--ghost" disabled={busy} onClick={() => void previewMapping()}>
            {busy && !preview ? "预览中…" : "预览影响"}
          </Button>
          <Button className="button button--primary" disabled={!preview || busy} onClick={() => void applyMapping()}>
            {busy && preview ? "应用中…" : "应用 Mapping"}
          </Button>
        </View>

        {preview ? (
          <View className="review-mobile-impact">
            <Text className="review-mobile-impact__title">影响预览</Text>
            <Text className="review-mobile-hint">Preview token 绑定当前 Mapping 选择和后端状态。</Text>
            {impact.map((line, index) => (
              <Text
                key={`${line.text}-${index}`}
                className={`review-mobile-impact__line${line.emphasis ? " review-mobile-impact__line--emphasis" : ""}`}
              >
                · {line.text}
              </Text>
            ))}
          </View>
        ) : null}
      </View>
    </View>
  );
}
