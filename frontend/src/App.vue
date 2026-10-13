<script setup>
import { computed, onMounted, ref } from "vue";
import { chatAboutNote, fetchHealth, ocrImage, summarizeNote } from "./api";

const note = ref("");
const summary = ref("");
const keyPoints = ref([]);
const healthText = ref("正在检查后端…");
const healthOk = ref(false);
const busy = ref("");
const errorText = ref("");
const infoText = ref("");
const chatInput = ref("");
const messages = ref([
  {
    role: "notice",
    label: "系统提示",
    text: "先粘贴笔记、上传文本或识别图片，再生成摘要，或基于当前笔记提问。",
  },
]);

const textFileInput = ref(null);
const imageFileInput = ref(null);

const hasNote = computed(() => note.value.trim().length > 0);
const isBusy = computed(() => Boolean(busy.value));

function clearFeedback() {
  errorText.value = "";
  infoText.value = "";
}

function setError(message) {
  errorText.value = message;
  infoText.value = "";
}

function setInfo(message) {
  infoText.value = message;
  errorText.value = "";
}

function appendMessage(role, label, text) {
  messages.value.push({ role, label, text });
}

async function refreshHealth() {
  try {
    const data = await fetchHealth();
    const ocr = data.ocr_ready ? "OCR 就绪" : "OCR 未就绪";
    healthText.value = `${data.message || "后端正常"} · ${data.model || "模型未知"} · ${ocr}`;
    healthOk.value = true;
  } catch (_error) {
    healthText.value = "后端未连接";
    healthOk.value = false;
  }
}

async function handleSummarize() {
  if (!hasNote.value || isBusy.value) {
    return;
  }
  clearFeedback();
  busy.value = "summarize";
  try {
    const data = await summarizeNote(note.value.trim());
    summary.value = data.summary || "";
    keyPoints.value = Array.isArray(data.key_points) ? data.key_points : [];
    setInfo("摘要已生成。");
  } catch (error) {
    setError(error.message || "摘要失败");
  } finally {
    busy.value = "";
  }
}

async function handleTextFile(event) {
  const file = event.target.files?.[0];
  event.target.value = "";
  if (!file || isBusy.value) {
    return;
  }
  clearFeedback();
  try {
    const text = await file.text();
    note.value = text.slice(0, 20000);
    setInfo(`已载入文本文件：${file.name}`);
  } catch (_error) {
    setError("读取文本文件失败，请确认是 UTF-8 文本。");
  }
}

async function handleImageFile(event) {
  const file = event.target.files?.[0];
  event.target.value = "";
  if (!file || isBusy.value) {
    return;
  }
  clearFeedback();
  busy.value = "ocr";
  try {
    const data = await ocrImage(file, true);
    note.value = data.text || "";
    summary.value = data.summary || "";
    keyPoints.value = Array.isArray(data.key_points) ? data.key_points : [];
    setInfo("图片文字已识别，并完成摘要。");
  } catch (error) {
    setError(error.message || "OCR 失败");
  } finally {
    busy.value = "";
  }
}

async function handleChat() {
  const message = chatInput.value.trim();
  if (!message || isBusy.value) {
    return;
  }
  if (!hasNote.value) {
    setError("请先准备当前笔记，再提问。");
    return;
  }

  clearFeedback();
  appendMessage("user", "我", message);
  chatInput.value = "";
  busy.value = "chat";
  appendMessage("assistant", "AI", "正在思考…");
  const loadingIndex = messages.value.length - 1;

  try {
    const data = await chatAboutNote(message, note.value.trim());
    messages.value[loadingIndex] = {
      role: "assistant",
      label: "AI",
      text: data.reply || "（无回复）",
    };
  } catch (error) {
    messages.value[loadingIndex] = {
      role: "notice",
      label: "系统提示",
      text: error.message || "问答失败",
    };
  } finally {
    busy.value = "";
  }
}

function onChatKeydown(event) {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    handleChat();
  }
}

onMounted(() => {
  refreshHealth();
});
</script>

<template>
  <header class="nav">
    <div class="nav-inner">
      <div class="brand">
        <span class="brand-mark" aria-hidden="true">笔</span>
        <div>
          <p class="brand-name">笔记摘要助手</p>
          <p class="brand-sub">摘要 · OCR · 基于笔记问答</p>
        </div>
      </div>
      <div class="nav-status">
        <span class="badge">Ollama</span>
        <span class="health" :class="{ ok: healthOk, error: !healthOk }">{{ healthText }}</span>
      </div>
    </div>
  </header>

  <main class="shell">
    <div v-if="errorText" class="status-banner error">{{ errorText }}</div>
    <div v-else-if="infoText" class="status-banner info">{{ infoText }}</div>

    <section class="workspace">
      <article class="panel">
        <div>
          <h2>当前笔记</h2>
          <p class="hint">粘贴内容、上传文本，或上传图片做 OCR。识别结果会写入这里。</p>
        </div>

        <div class="toolbar">
          <button class="btn secondary" type="button" :disabled="isBusy" @click="textFileInput?.click()">
            上传文本
          </button>
          <button class="btn secondary" type="button" :disabled="isBusy" @click="imageFileInput?.click()">
            {{ busy === "ocr" ? "识别中…" : "上传图片 OCR" }}
          </button>
          <button class="btn" type="button" :disabled="!hasNote || isBusy" @click="handleSummarize">
            {{ busy === "summarize" ? "摘要中…" : "生成摘要" }}
          </button>
          <button
            class="btn ghost"
            type="button"
            :disabled="isBusy || (!note && !summary)"
            @click="note = ''; summary = ''; keyPoints = []; clearFeedback()"
          >
            清空
          </button>
        </div>

        <label class="sr-only" for="note-input">笔记内容</label>
        <textarea
          id="note-input"
          v-model="note"
          class="note-input"
          maxlength="20000"
          placeholder="在这里粘贴课堂笔记、会议记录或任意文本…"
          :disabled="isBusy"
        />

        <input
          ref="textFileInput"
          class="sr-only"
          type="file"
          accept=".txt,text/plain"
          @change="handleTextFile"
        />
        <input
          ref="imageFileInput"
          class="sr-only"
          type="file"
          accept="image/png,image/jpeg,image/webp,image/bmp,image/gif"
          @change="handleImageFile"
        />
      </article>

      <div class="stack">
        <article class="panel">
          <div>
            <h2>摘要与要点</h2>
            <p class="hint">由本地 Ollama 模型根据当前笔记生成。</p>
          </div>
          <div class="summary-box">
            <p :class="{ filled: summary }">{{ summary || "尚未生成摘要。" }}</p>
          </div>
          <div class="points-box">
            <ul v-if="keyPoints.length">
              <li v-for="(point, index) in keyPoints" :key="`${index}-${point}`">{{ point }}</li>
            </ul>
            <p v-else class="empty">要点会显示在这里。</p>
          </div>
        </article>

        <article class="panel">
          <div>
            <h2>基于笔记问答</h2>
            <p class="hint">例如：「这段主要在讲什么」「帮我解释第 2 点」。</p>
          </div>

          <div class="messages" aria-live="polite">
            <article
              v-for="(item, index) in messages"
              :key="`${index}-${item.role}`"
              class="message"
              :class="item.role"
            >
              <div class="bubble">
                <span class="meta">{{ item.label }}</span>
                <span class="body">{{ item.text }}</span>
              </div>
            </article>
          </div>

          <form class="composer" @submit.prevent="handleChat">
            <label class="sr-only" for="chat-input">提问</label>
            <textarea
              id="chat-input"
              v-model="chatInput"
              class="chat-input"
              rows="2"
              maxlength="4000"
              placeholder="针对当前笔记提问，Enter 发送，Shift+Enter 换行"
              :disabled="isBusy"
              @keydown="onChatKeydown"
            />
            <button class="btn" type="submit" :disabled="!chatInput.trim() || !hasNote || isBusy">
              {{ busy === "chat" ? "发送中…" : "发送" }}
            </button>
          </form>
        </article>
      </div>
    </section>
  </main>
</template>
