async function parseError(response, fallback) {
  const data = await response.json().catch(() => null);
  if (!data) {
    return fallback;
  }
  const detail = data.detail;
  if (typeof detail === "string" && detail.trim()) {
    return detail;
  }
  if (Array.isArray(detail)) {
    const joined = detail
      .map((item) => item.msg || item)
      .filter(Boolean)
      .join("；");
    if (joined) {
      return joined;
    }
  }
  return fallback;
}

export async function fetchHealth() {
  const response = await fetch("/api/health");
  if (!response.ok) {
    throw new Error(await parseError(response, "健康检查失败"));
  }
  return response.json();
}

export async function summarizeNote(text) {
  const response = await fetch("/api/summarize", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!response.ok) {
    throw new Error(await parseError(response, "摘要失败，请稍后重试"));
  }
  return response.json();
}

export async function ocrImage(file, summarizeAfter = true) {
  const form = new FormData();
  form.append("file", file);
  const query = summarizeAfter ? "?summarize_after=true" : "?summarize_after=false";
  const response = await fetch(`/api/ocr${query}`, {
    method: "POST",
    body: form,
  });
  if (!response.ok) {
    throw new Error(await parseError(response, "OCR 识别失败，请稍后重试"));
  }
  return response.json();
}

export async function chatAboutNote(message, context) {
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, context }),
  });
  if (!response.ok) {
    throw new Error(await parseError(response, "问答失败，请稍后重试"));
  }
  return response.json();
}
