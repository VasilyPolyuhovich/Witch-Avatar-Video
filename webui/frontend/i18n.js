const TRANSLATIONS = {
  en: {
    title: "Witch Avatar Video",
    start: "Start",
    stop: "Stop",
    generate: "Generate",
    statusNotStarted: "Not started",
    statusConnecting: "Connecting...",
    statusReady: "Ready",
    statusGenerating: "Generating...",
    elapsedLabel: "Running for",
    photoLabel: "Photo",
    textLabel: "Text",
    voiceLabel: "Voice sample (optional)",
    startFailed: "Could not start -- please try again.",
    generateFailedGeneric: "Generation failed -- please try again.",
    errorInvalidInput: "The photo or text couldn't be used -- please check them and try again.",
    errorOutOfMemory: "The server ran out of resources -- please try again.",
    longSessionWarning: "This session has been running for a while -- consider stopping it if you're done.",
  },
  uk: {
    title: "Аватар-відео гадалки",
    start: "Запустити",
    stop: "Зупинити",
    generate: "Згенерувати",
    statusNotStarted: "Не запущено",
    statusConnecting: "З'єднання...",
    statusReady: "Готово",
    statusGenerating: "Генерація...",
    elapsedLabel: "Триває",
    photoLabel: "Фото",
    textLabel: "Текст",
    voiceLabel: "Зразок голосу (опційно)",
    startFailed: "Не вдалось запустити -- спробуйте ще раз.",
    generateFailedGeneric: "Генерація не вдалась -- спробуйте ще раз.",
    errorInvalidInput: "Фото чи текст не підійшли -- перевірте їх і спробуйте ще раз.",
    errorOutOfMemory: "Серверу не вистачило ресурсів -- спробуйте ще раз.",
    longSessionWarning: "Ця сесія триває вже довго -- зупиніть її, якщо робота завершена.",
  },
};

const ERROR_CODE_TO_KEY = {
  invalid_input: "errorInvalidInput",
  out_of_memory: "errorOutOfMemory",
  generation_failed: "generateFailedGeneric",
};

function currentLanguage() {
  const stored = localStorage.getItem("webui_language");
  if (stored === "en" || stored === "uk") return stored;
  const browserLang = navigator.language.slice(0, 2);
  return browserLang === "en" ? "en" : "uk";
}

function setLanguage(lang) {
  localStorage.setItem("webui_language", lang);
}

function t(key) {
  return TRANSLATIONS[currentLanguage()][key] || key;
}

function errorMessageFor(errorCode) {
  const key = ERROR_CODE_TO_KEY[errorCode] || "generateFailedGeneric";
  return t(key);
}
