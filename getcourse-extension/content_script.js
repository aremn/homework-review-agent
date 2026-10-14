"use strict";
console.log("GetCourse AI Prompt Helper: Content script loaded.");

const AI_BUTTON_ID = "gc-ai-prompt-button";
const PROMPT_CONTAINER_ID = "gc-ai-prompt-container";
const PROMPT_TEXTAREA_ID = "gc-ai-prompt-textarea";
const PROMPT_SAVE_BUTTON_ID = "gc-ai-prompt-save-button";
const SAVE_STATUS_ID = "gc-ai-prompt-save-status";

function getLessonIdFromUrl() {
  const urlParams = new URLSearchParams(window.location.search);
  const lessonId = urlParams.get("id");
  console.log("AI Helper: Extracted lesson ID from URL parameter 'id':", lessonId);
  return lessonId;
}

function createAiButton() {
  const button = document.createElement("button");
  button.id = AI_BUTTON_ID;
  button.type = "button";
  button.className = "btn btn-default";
  button.innerHTML = '<span class="fa fa-magic"></span> AI Промпт';
  button.style.marginLeft = "5px";
  button.onclick = togglePromptContainer;
  return button;
}

function createPromptContainer(lessonId) {
  const container = document.createElement("div");
  container.id = PROMPT_CONTAINER_ID;
  container.className = "gc-ai-prompt-container hidden";
  const label = document.createElement("label");
  label.htmlFor = PROMPT_TEXTAREA_ID;
  label.textContent = `Промпт для урока ID ${lessonId || "???"}:`;
  label.style.fontWeight = "bold";
  label.style.marginBottom = "5px";
  label.style.display = "block";
  const textarea = document.createElement("textarea");
  textarea.id = PROMPT_TEXTAREA_ID;
  textarea.placeholder =
    "Введите системный промпт для GPT... \nМожно использовать {history} для вставки истории.";
  textarea.style.resize = "none";
  textarea.style.width = "100%";
  textarea.style.minHeight = "120px";
  textarea.style.fontSize = "14px";
  textarea.style.boxSizing = "border-box";
  textarea.style.marginBottom = "10px";
  textarea.rows = 7;
  const saveButton = document.createElement("button");
  saveButton.id = PROMPT_SAVE_BUTTON_ID;
  saveButton.type = "button";
  saveButton.className = "btn btn-primary";
  saveButton.textContent = "Сохранить промпт";
  saveButton.onclick = () => savePromptViaBackground(lessonId);
  const saveStatus = document.createElement("span");
  saveStatus.id = SAVE_STATUS_ID;
  saveStatus.className = "save-status";
  container.appendChild(label);
  container.appendChild(textarea);
  container.appendChild(saveButton);
  container.appendChild(saveStatus);
  return container;
}

function togglePromptContainer() {
  const container = document.getElementById(PROMPT_CONTAINER_ID);
  if (!container) return;
  container.classList.toggle("hidden");
  if (!container.classList.contains("hidden")) {
    console.log("Prompt container shown, loading...");
    const lessonId = getLessonIdFromUrl();
    if (lessonId) loadPromptViaBackground(lessonId);
    else {
      console.warn("Lesson ID not found.");
      const textarea = document.getElementById(PROMPT_TEXTAREA_ID);
      if (textarea) textarea.value = "// Не удалось найти ID урока.";
    }
  } else console.log("Prompt container hidden.");
}

async function savePromptViaBackground(lessonId) {
  const textarea = document.getElementById(PROMPT_TEXTAREA_ID);
  const statusSpan = document.getElementById(SAVE_STATUS_ID);
  if (!textarea || !lessonId || !statusSpan) {
    console.error("Save error: Elements missing.");
    if (statusSpan) {
      statusSpan.textContent = "Ошибка!";
      statusSpan.style.color = "red";
    }
    return;
  }
  const promptText = textarea.value;
  statusSpan.textContent = "Сохранение...";
  statusSpan.style.color = "orange";
  try {
    const response = await chrome.runtime.sendMessage({
      action: "savePrompt",
      payload: { lesson_id: String(lessonId), prompt_text: promptText },
    });
    if (response && response.success) {
      console.log(`Prompt ${lessonId} saved.`);
      statusSpan.textContent = "Сохранено!";
      statusSpan.style.color = "green";
      setTimeout(() => {
        if (statusSpan) statusSpan.textContent = "";
      }, 3000);
    } else {
      throw new Error(response?.error || "Ошибка background script");
    }
  } catch (error) {
    console.error("Save error:", error);
    statusSpan.textContent = `Ошибка: ${error.message}`;
    statusSpan.style.color = "red";
  }
}

async function loadPromptViaBackground(lessonId) {
  const textarea = document.getElementById(PROMPT_TEXTAREA_ID);
  const statusSpan = document.getElementById(SAVE_STATUS_ID);
  if (!textarea || !lessonId || !statusSpan) {
    console.warn("Load error: Elements missing.");
    return;
  }
  textarea.value = "Загрузка...";
  statusSpan.textContent = "";
  console.log(`Loading prompt for lesson ${lessonId}`);
  try {
    const response = await chrome.runtime.sendMessage({
      action: "loadPrompt",
      payload: { lessonId: String(lessonId) },
    });
    if (response && response.success) {
      console.log("ContentScript: Success response from background for loadPrompt:", response);
      textarea.value = response.promptText || "";
      console.log(`ContentScript: Textarea value set to: [${textarea.value}]`);
      console.log(`Prompt ${lessonId} loaded.`);
    } else if (response && response.notFound) {
      textarea.value = "";
      console.log(`No prompt found for ${lessonId}.`);
    } else {
      throw new Error(response?.error || "Ошибка background при загрузке");
    }
  } catch (error) {
    console.error("Load error:", error);
    textarea.value = `// Ошибка загрузки: ${error.message}`;
    statusSpan.textContent = `Ошибка загрузки!`;
    statusSpan.style.color = "red";
  }
}

function initExtensionUI() {
  const targetToolbar = document.querySelector(
    'div.lite-page-footer-add-block div.btn-group[role="group"]',
  );
  if (targetToolbar && !document.getElementById(AI_BUTTON_ID)) {
    const lessonId = getLessonIdFromUrl();
    if (!lessonId) {
      console.warn("AI Helper: Lesson ID not found.");
      return;
    }
    const aiButton = createAiButton();
    const promptContainer = createPromptContainer(lessonId);
    targetToolbar.appendChild(aiButton);
    const parentContainer =
      targetToolbar.closest(".lite-page-footer-add-block") || targetToolbar.parentNode;
    if (parentContainer && parentContainer.parentNode) {
      parentContainer.parentNode.insertBefore(promptContainer, parentContainer.nextSibling);
    } else {
      console.error("Could not find parent for prompt container.");
    }
    console.log("AI Helper UI initialized for lesson:", lessonId);
  }
}

initExtensionUI();
setTimeout(initExtensionUI, 1500);
setTimeout(initExtensionUI, 3000);
