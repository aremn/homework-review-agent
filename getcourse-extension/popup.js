"use strict";
document.addEventListener("DOMContentLoaded", () => {
  console.log("Popup: DOMContentLoaded");
  const statusMessage = document.getElementById("status-message");
  const loginView = document.getElementById("auth-login-view");
  const loggedInView = document.getElementById("auth-loggedin-view");
  const emailInput = document.getElementById("email");
  const passwordInput = document.getElementById("password");
  const loginButton = document.getElementById("login-button");
  const signupButton = document.getElementById("signup-button");
  const logoutButton = document.getElementById("logout-button");
  const userEmailSpan = document.getElementById("user-email");
  const authErrorDiv = document.getElementById("auth-error");
  const promptsListDiv = document.getElementById("prompts-list");
  const promptStatus = document.getElementById("prompt-status");
  const editModal = document.getElementById("edit-prompt-modal");
  const editLessonIdSpan = document.getElementById("edit-lesson-id");
  const editTextArea = document.getElementById("edit-prompt-textarea");
  const saveEditButton = document.getElementById("save-edit-button");
  const cancelEditButton = document.getElementById("cancel-edit-button");
  const editStatusMessage = document.getElementById("edit-status-message");
  const addGcAccountButton = document.getElementById("add-gc-account-button");
  const gcAccountsListDiv = document.getElementById("gc-accounts-list");
  const gcAccountStatus = document.getElementById("gc-account-status");
  const gcAccountModal = document.getElementById("gc-account-modal");
  const gcModalTitle = document.getElementById("gc-modal-title");
  const editGcAccountIdInput = document.getElementById("edit-gc-account-id");
  const gcSubdomainInput = document.getElementById("gc-subdomain");
  const gcEmailInput = document.getElementById("gc-email");
  const gcPasswordInput = document.getElementById("gc-password");
  const gcIsActiveCheckbox = document.getElementById("gc-is-active");
  const saveGcAccountButton = document.getElementById("save-gc-account-button");
  const cancelGcAccountButton = document.getElementById("cancel-gc-account-button");
  const gcModalError = document.getElementById("gc-modal-error");
  const addCourseVariableButton = document.getElementById("add-course-variable-button");
  const cvGcAccountSelect = document.getElementById("cv-gc-account-select");
  const courseVariablesListDiv = document.getElementById("course-variables-list");
  const courseVariableStatus = document.getElementById("course-variable-status");
  const courseVariableModal = document.getElementById("course-variable-modal");
  const cvModalTitle = document.getElementById("cv-modal-title");
  const editCvIdInput = document.getElementById("edit-cv-id");
  const editCvGcAccountIdInput = document.getElementById("edit-cv-gc-account-id");
  const cvNameInput = document.getElementById("cv-name");
  const cvValueInput = document.getElementById("cv-value");
  const saveCourseVariableButton = document.getElementById("save-course-variable-button");
  const cancelCourseVariableButton = document.getElementById("cancel-course-variable-button");
  const cvModalError = document.getElementById("cv-modal-error");

  const elements = {
    statusMessage,
    loginView,
    loggedInView,
    emailInput,
    passwordInput,
    loginButton,
    signupButton,
    logoutButton,
    userEmailSpan,
    authErrorDiv,
    promptsListDiv,
    promptStatus,
    editModal,
    editLessonIdSpan,
    editTextArea,
    saveEditButton,
    cancelEditButton,
    editStatusMessage,
    addGcAccountButton,
    gcAccountsListDiv,
    gcAccountStatus,
    gcAccountModal,
    gcModalTitle,
    editGcAccountIdInput,
    gcSubdomainInput,
    gcEmailInput,
    gcPasswordInput,
    gcIsActiveCheckbox,
    saveGcAccountButton,
    cancelGcAccountButton,
    gcModalError,
    addCourseVariableButton,
    cvGcAccountSelect,
    courseVariablesListDiv,
    courseVariableStatus,
    courseVariableModal,
    cvModalTitle,
    editCvIdInput,
    editCvGcAccountIdInput,
    cvNameInput,
    cvValueInput,
    saveCourseVariableButton,
    cancelCourseVariableButton,
    cvModalError,
  };
  for (const key in elements) {
    if (!elements[key]) {
      console.error(`Popup Error: Element ${key} not found.`);
      if (statusMessage) statusMessage.textContent = `Ошибка UI: Элемент ${key} не найден.`;
      return;
    }
  }
  statusMessage.textContent = "Инициализация...";
  let editingLessonId = null;
  let currentGcAccounts = [];
  let currentCourseVariables = [];
  let selectedGcAccountIdForVariables = null;

  let promptsCache = null;
  let gcAccountsCache = null;
  let courseVariablesCache = {};

  async function sendMessageToBackground(action, payload = {}) {
    console.log(`Popup: Sending action "${action}"`, payload);
    try {
      if (action.startsWith("sign") || action === "getSession")
        statusMessage.textContent = "Обработка...";
      else if (action.startsWith("load")) statusMessage.textContent = "Загрузка...";
      else if (action.startsWith("save")) statusMessage.textContent = "Сохранение...";
      else if (action.startsWith("delete")) statusMessage.textContent = "Удаление...";
      statusMessage.style.color = "var(--text-muted-color)";
      const response = await chrome.runtime.sendMessage({ action, payload });
      console.log(`Popup: Received response for "${action}":`, response);
      if (response === undefined) throw new Error(`Нет ответа от background для "${action}".`);
      if (response && response.success) {
        statusMessage.textContent = "";
        return response;
      } else {
        throw new Error(response?.error || `Ошибка от background для "${action}"`);
      }
    } catch (error) {
      console.error(`Popup Error (Action: ${action}):`, error);
      const errorMsg = `Ошибка (${action}): ${error.message || "Нет ответа."}`;
      if (action.includes("Prompt")) {
        if (promptStatus) {
          promptStatus.textContent = errorMsg;
          promptStatus.style.color = "var(--danger-color)";
        }
      } else if (action.includes("GetCourse") || action.includes("Account")) {
        if (gcAccountStatus) {
          gcAccountStatus.textContent = errorMsg;
          gcAccountStatus.style.color = "var(--danger-color)";
        }
      } else {
        if (statusMessage) {
          statusMessage.textContent = errorMsg;
          statusMessage.style.color = "var(--danger-color)";
        }
        if (authErrorDiv && action.startsWith("sign")) {
          authErrorDiv.textContent = error.message || "Ошибка аутентификации";
        }
      }
      if (statusMessage && statusMessage.textContent === "Обработка...")
        statusMessage.textContent = "";
      throw error;
    }
  }

  function showView(viewToShow) {
    loginView.classList.add("hidden");
    loggedInView.classList.add("hidden");
    if (viewToShow === "login") loginView.classList.remove("hidden");
    else if (viewToShow === "loggedIn") loggedInView.classList.remove("hidden");
    if (authErrorDiv) authErrorDiv.textContent = "";
    if (promptStatus) promptStatus.textContent = "";
    if (gcAccountStatus) gcAccountStatus.textContent = "";
    if (statusMessage && statusMessage.style.color !== "var(--danger-color)")
      statusMessage.textContent = "";
  }

  async function checkSessionAndUpdateUI() {
    try {
      const response = await sendMessageToBackground("getSession");
      if (response.session) {
        userEmailSpan.textContent = response.session.user?.email ?? "Неизвестно";
        showView("loggedIn");
        await Promise.all([displayPrompts(), displayGetCourseAccounts()]);
      } else {
        showView("login");
        statusMessage.textContent = "Войдите или зарегистрируйтесь.";
      }
    } catch (error) {
      showView("login");
    }
  }

  async function handleLogin() {
    const email = emailInput.value.trim();
    const password = passwordInput.value;
    if (!email || !password) {
      authErrorDiv.textContent = "Введите email и пароль.";
      return;
    }
    authErrorDiv.textContent = "";
    try {
      await sendMessageToBackground("signIn", { email, password });
      await checkSessionAndUpdateUI();
    } catch (error) {}
  }
  async function handleSignup() {
    const email = emailInput.value.trim();
    const password = passwordInput.value;
    if (!email || !password) {
      authErrorDiv.textContent = "Введите email и пароль.";
      return;
    }
    authErrorDiv.textContent = "";
    try {
      const response = await sendMessageToBackground("signUp", { email, password });
      if (response.data?.user && response.data.session) {
        await checkSessionAndUpdateUI();
        statusMessage.textContent = "Успешно! Вход выполнен.";
        statusMessage.style.color = "var(--success-color)";
      } else {
        showView("login");
        statusMessage.textContent = "Аккаунт создан. Проверьте почту.";
        statusMessage.style.color = "var(--info-color)";
        passwordInput.value = "";
      }
    } catch (error) {}
  }

  async function handleLogout() {
    try {
      await sendMessageToBackground("signOut");
      await checkSessionAndUpdateUI();
    } catch (error) {}
  }

  function openEditModal(promptObject) {
    const modalTitleElement = document.getElementById("edit-prompt-modal-title");
    const lessonIdSpanElement = document.getElementById("edit-lesson-id");
    const promptDbIdInputElement = document.getElementById("edit-prompt-db-id");
    const promptTextareaElement = document.getElementById("edit-prompt-textarea");
    const maxMessagesElement = document.getElementById("edit-max-messages");
    const finalMessageTextElement = document.getElementById("edit-final-message-text");
    const finalPromptSelectElement = document.getElementById("edit-final-prompt-select");
    const modalStatusMessageElement = document.getElementById("edit-status-message");
    const modalElement = document.getElementById("edit-prompt-modal");

    if (
      !modalTitleElement ||
      !lessonIdSpanElement ||
      !promptDbIdInputElement ||
      !promptTextareaElement ||
      !maxMessagesElement ||
      !finalMessageTextElement ||
      !finalPromptSelectElement ||
      !modalStatusMessageElement ||
      !modalElement
    ) {
      console.error(
        "openEditModal: Один или несколько элементов модального окна не найдены! Проверьте ID в HTML и JS.",
      );
      alert("Ошибка интерфейса: не удалось открыть окно редактирования промпта.");
      return;
    }

    if (!promptObject || !promptObject.lesson_id) {
      console.error(
        "openEditModal: Некорректные данные промпта переданы (promptObject или lesson_id отсутствуют).",
      );
      modalStatusMessageElement.textContent = "Ошибка: Не удалось загрузить данные промпта.";
      modalStatusMessageElement.style.color = "red";
      modalElement.classList.remove("hidden");
      return;
    }

    editingLessonId = promptObject.lesson_id;
    modalTitleElement.textContent = `Редактировать промпт для урока ID ${promptObject.lesson_id}`;
    lessonIdSpanElement.textContent = promptObject.lesson_id;
    promptDbIdInputElement.value = promptObject.id || "";

    promptTextareaElement.value = promptObject.prompt_text || "";
    maxMessagesElement.value =
      promptObject.max_messages_in_thread === null ||
      promptObject.max_messages_in_thread === undefined
        ? ""
        : promptObject.max_messages_in_thread;
    finalMessageTextElement.value = promptObject.final_message_text || "";
    populateFinalPromptSelect(promptObject.use_final_message_prompt_id, promptObject.id).then(
      () => {
        if (promptObject.use_final_message_prompt_id) {
          finalPromptSelectElement.value = promptObject.use_final_message_prompt_id;
        }
      },
    );
    modalStatusMessageElement.textContent = "";
    modalElement.classList.remove("hidden");
  }

  async function populateFinalPromptSelect(selectedFinalPromptId, currentEditingPromptId) {
    const finalPromptSelectElement = document.getElementById("edit-final-prompt-select");
    if (!finalPromptSelectElement) {
      console.error(
        "populateFinalPromptSelect: Select element 'edit-final-prompt-select' not found.",
      );
      return;
    }

    finalPromptSelectElement.innerHTML =
      '<option value="">-- Использовать статический текст --</option>';

    try {
      const response = await sendMessageToBackground("loadAllPrompts");
      const allUserPrompts = response.prompts || [];

      if (!Array.isArray(allUserPrompts)) {
        console.error("populateFinalPromptSelect: Invalid prompt list received from background.");
        return;
      }

      allUserPrompts.forEach((prompt) => {
        if (prompt.id && prompt.id !== currentEditingPromptId) {
          const option = document.createElement("option");
          option.value = prompt.id;
          let displayText = `Урок ${prompt.lesson_id || "Default"}`;
          if (prompt.prompt_text) {
            displayText += `: ${prompt.prompt_text.substring(0, 40)}${prompt.prompt_text.length > 40 ? "..." : ""}`;
          }
          option.textContent = displayText;
          finalPromptSelectElement.appendChild(option);
        }
      });

      if (selectedFinalPromptId) {
        finalPromptSelectElement.value = selectedFinalPromptId;
      }
    } catch (error) {
      console.error("Error populating final prompt select:", error);
      const errorOption = document.createElement("option");
      errorOption.value = "";
      errorOption.textContent = "Ошибка загрузки списка промптов";
      errorOption.disabled = true;
      finalPromptSelectElement.appendChild(errorOption);
    }
  }

  function closeEditModal() {
    editingLessonId = null;
    editModal.classList.add("hidden");
  }

  async function handleSaveEdit() {
    const lessonIdForSave = document.getElementById("edit-lesson-id").textContent;

    const newPromptText = document.getElementById("edit-prompt-textarea").value;
    const maxMessages = parseInt(document.getElementById("edit-max-messages").value) || null;
    const finalMessageText =
      document.getElementById("edit-final-message-text").value.trim() || null;
    const finalPromptId = document.getElementById("edit-final-prompt-select").value || null;

    if (!lessonIdForSave) {
      editStatusMessage.textContent = "Критическая ошибка: ID урока не определен для сохранения!";
      editStatusMessage.style.color = "red";
      console.error("handleSaveEdit: lessonIdForSave is missing or empty!");
      return;
    }

    editStatusMessage.textContent = "Сохранение...";
    editStatusMessage.style.color = "orange";
    try {
      const payload = {
        lesson_id: lessonIdForSave,
        prompt_text: newPromptText,
        max_messages_in_thread: maxMessages,
        final_message_text: finalMessageText,
        use_final_message_prompt_id: finalPromptId,
      };
      console.log("Popup handleSaveEdit: Sending payload to savePrompt:", payload);

      await sendMessageToBackground("savePrompt", payload);
      editStatusMessage.textContent = "Промпт сохранен!";
      editStatusMessage.style.color = "green";
      closeEditModal();
      promptsCache = null;
      await displayPrompts(true);
    } catch (error) {
      editStatusMessage.textContent = `Ошибка сохранения: ${error.message}`;
      editStatusMessage.style.color = "red";
      console.error("Popup handleSaveEdit: Error saving prompt:", error);
    }
  }

  async function handleDeletePrompt(lessonId) {
    const confirmText = lessonId === "default" ? "дефолтный промпт" : `промпт урока ${lessonId}`;
    if (confirm(`Удалить ${confirmText}?`)) {
      promptStatus.textContent = "Удаление...";
      promptStatus.style.color = "orange";
      try {
        await sendMessageToBackground("deletePrompt", { lessonId });
        promptStatus.textContent = "Промпт удален.";
        promptStatus.style.color = "green";
        promptsCache = null;
        await displayPrompts(true);
        setTimeout(() => {
          if (promptStatus && promptStatus.style.color === "var(--success-color)")
            promptStatus.textContent = "";
        }, 2000);
      } catch (error) {}
    }
  }
  async function displayPrompts(forceReload = false) {
    promptsListDiv.innerHTML = "<p>Загрузка промптов...</p>";
    try {
      let items;
      if (!forceReload && promptsCache) {
        items = promptsCache;
      } else {
        const response = await sendMessageToBackground("loadAllPrompts");
        items = response.prompts;
        promptsCache = items;
      }
      if (!Array.isArray(items)) throw new Error("Неверный формат списка.");
      if (items.length === 0) {
        promptsListDiv.innerHTML = "<p>Нет сохраненных промптов.</p>";
        return;
      }
      promptsListDiv.innerHTML = "";
      items.forEach((item) => {
        const lessonId = item.lesson_id;
        const promptText = item.prompt_text;
        const itemDiv = document.createElement("div");
        itemDiv.className = "prompt-item";
        const keySpan = document.createElement("span");
        keySpan.className = "prompt-key";
        keySpan.textContent = `ID: ${lessonId}`;
        const textSpan = document.createElement("span");
        textSpan.className = "prompt-text";
        textSpan.textContent = promptText || "[Пустой]";
        textSpan.title = promptText;
        const actionsDiv = document.createElement("div");
        actionsDiv.className = "prompt-actions";
        const editButton = document.createElement("button");
        editButton.title = "Редактировать";
        editButton.innerHTML = '<i class="fa-solid fa-pencil"></i>';
        editButton.onclick = () => openEditModal(item);
        const deleteButton = document.createElement("button");
        deleteButton.title = "Удалить";
        deleteButton.className = "delete-btn";
        deleteButton.innerHTML = '<i class="fa-regular fa-trash-can"></i>';
        deleteButton.onclick = () => handleDeletePrompt(lessonId);
        actionsDiv.appendChild(editButton);
        actionsDiv.appendChild(deleteButton);
        itemDiv.appendChild(actionsDiv);
        itemDiv.appendChild(keySpan);
        itemDiv.appendChild(textSpan);
        promptsListDiv.appendChild(itemDiv);
      });
    } catch (error) {
      promptsListDiv.innerHTML = `<p style="color:red;">Ошибка загрузки промптов.</p>`;
    }
  }

  async function displayGetCourseAccounts(forceReload = false) {
    gcAccountsListDiv.innerHTML = "<p>Загрузка аккаунтов...</p>";
    gcAccountStatus.textContent = "";
    cvGcAccountSelect.innerHTML = '<option value="">-- Выберите аккаунт --</option>';
    try {
      let accounts;
      if (!forceReload && gcAccountsCache) {
        accounts = gcAccountsCache;
      } else {
        const response = await sendMessageToBackground("listGetCourseAccounts");
        accounts = response.accounts || [];
        gcAccountsCache = accounts;
      }
      currentGcAccounts = accounts;
      if (!Array.isArray(currentGcAccounts)) {
        throw new Error("Background script did not return a valid account list.");
      }
      if (currentGcAccounts.length === 0) {
        gcAccountsListDiv.innerHTML = "<p>Нет добавленных аккаунтов GetCourse.</p>";
        courseVariablesListDiv.innerHTML = "<p>Сначала добавьте аккаунт GetCourse.</p>";
        selectedGcAccountIdForVariables = null;
        return;
      }
      gcAccountsListDiv.innerHTML = "";
      currentGcAccounts.forEach((acc) => {
        const option = document.createElement("option");
        option.value = acc.id;
        option.textContent = `${acc.subdomain}.getcourse.ru`;
        cvGcAccountSelect.appendChild(option);
      });
      if (
        selectedGcAccountIdForVariables &&
        currentGcAccounts.find((a) => a.id === selectedGcAccountIdForVariables)
      ) {
        cvGcAccountSelect.value = selectedGcAccountIdForVariables;
        displayCourseVariables(selectedGcAccountIdForVariables);
      } else {
        selectedGcAccountIdForVariables = null;
        courseVariablesListDiv.innerHTML = "<p>Выберите аккаунт для просмотра переменных.</p>";
      }
      currentGcAccounts.forEach((acc) => {
        const itemDiv = document.createElement("div");
        itemDiv.className = "gc-account-item";
        const infoDiv = document.createElement("div");
        infoDiv.className = "gc-account-info";
        const subdomainSpan = document.createElement("span");
        subdomainSpan.innerHTML = `Домен: <strong>${acc.subdomain || "???"}.getcourse.ru</strong>`;
        const emailSpan = document.createElement("span");
        emailSpan.innerHTML = `Email: <strong>${acc.email || "???"}</strong>`;
        const statusSpan = document.createElement("span");
        statusSpan.className = "status";
        if (acc.is_currently_processing) {
          statusSpan.innerHTML =
            '<i class="fa-solid fa-spinner fa-spin" style="margin-right: 5px;"></i> В обработке...';
          statusSpan.classList.add("status-processing");
          statusSpan.style.color = "var(--info-color)";
        } else if (acc.last_verification_status === "failed") {
          statusSpan.textContent = "Статус: Ошибка входа (неверные данные)";
          statusSpan.classList.add("status-failed");
        } else if (!acc.is_active) {
          statusSpan.textContent = "Статус: Неактивен";
          statusSpan.classList.add("status-inactive");
        } else if (acc.last_verification_status === "success") {
          statusSpan.textContent = "Статус: Проверен";
          statusSpan.classList.add("status-success");
        } else {
          statusSpan.textContent = "Статус: Ожидает проверки";
          statusSpan.classList.add("status-pending");
        }
        infoDiv.appendChild(subdomainSpan);
        infoDiv.appendChild(emailSpan);
        infoDiv.appendChild(statusSpan);
        const actionsDiv = document.createElement("div");
        actionsDiv.className = "gc-account-actions";
        const toggleActiveButton = document.createElement("button");
        actionsDiv.appendChild(toggleActiveButton);
        const editButton = document.createElement("button");
        actionsDiv.appendChild(editButton);
        const deleteButton = document.createElement("button");
        actionsDiv.appendChild(deleteButton);
        toggleActiveButton.title = acc.is_active ? "Деактивировать" : "Активировать";
        toggleActiveButton.innerHTML = acc.is_active
          ? '<i class="fa-solid fa-toggle-on" style="color: var(--success-color);"></i>'
          : '<i class="fa-solid fa-toggle-off" style="color: orange;"></i>';
        toggleActiveButton.onclick = () => handleToggleActiveGcAccount(acc.id, !acc.is_active);
        toggleActiveButton.disabled = acc.is_currently_processing;
        editButton.title = "Редактировать";
        editButton.innerHTML = '<i class="fa-solid fa-pencil"></i>';
        editButton.onclick = () => openGcAccountModal(acc.id);
        editButton.disabled = acc.is_currently_processing;
        deleteButton.title = "Удалить";
        deleteButton.className = "delete-btn";
        deleteButton.innerHTML = '<i class="fa-regular fa-trash-can"></i>';
        deleteButton.onclick = () => handleDeleteGcAccount(acc.id, acc.subdomain);
        deleteButton.disabled = acc.is_currently_processing;
        itemDiv.appendChild(infoDiv);
        itemDiv.appendChild(actionsDiv);
        gcAccountsListDiv.appendChild(itemDiv);
      });
    } catch (error) {
      gcAccountsListDiv.innerHTML = `<p style="color:red;">Ошибка загрузки аккаунтов.</p>`;
      courseVariablesListDiv.innerHTML =
        '<p style="color:red;">Ошибка загрузки аккаунтов, переменные не доступны.</p>';
      console.error("Error displaying GC accounts:", error);
    }
  }

  async function displayCourseVariables(gcAccountId, forceReload = false) {
    if (!gcAccountId) {
      courseVariablesListDiv.innerHTML =
        "<p>Выберите аккаунт GetCourse для просмотра переменных.</p>";
      currentCourseVariables = [];
      return;
    }
    selectedGcAccountIdForVariables = gcAccountId;
    courseVariablesListDiv.innerHTML = "<p>Загрузка переменных...</p>";
    courseVariableStatus.textContent = "";
    try {
      let variables;
      if (!forceReload && courseVariablesCache[gcAccountId]) {
        variables = courseVariablesCache[gcAccountId];
      } else {
        const response = await sendMessageToBackground("listCourseVariables", {
          getcourse_account_id: gcAccountId,
        });
        variables = response.variables || [];
        courseVariablesCache[gcAccountId] = variables;
      }
      currentCourseVariables = variables;
      if (!Array.isArray(currentCourseVariables))
        throw new Error("Неверный формат списка переменных.");
      if (currentCourseVariables.length === 0) {
        courseVariablesListDiv.innerHTML = "<p>Для этого аккаунта переменных нет.</p>";
        return;
      }
      courseVariablesListDiv.innerHTML = "";
      currentCourseVariables.forEach((cv) => {
        const itemDiv = document.createElement("div");
        itemDiv.className = "cv-item";
        const nameSpan = document.createElement("span");
        nameSpan.className = "cv-name";
        nameSpan.textContent = `{{${cv.variable_name}}}`;
        nameSpan.title = cv.variable_name;
        const valueSpan = document.createElement("div");
        valueSpan.className = "cv-value";
        valueSpan.textContent = cv.variable_value || "[Пустое значение]";
        valueSpan.title = cv.variable_value;

        const actionsDiv = document.createElement("div");
        actionsDiv.className = "cv-actions";
        const editBtn = document.createElement("button");
        editBtn.title = "Редактировать";
        editBtn.innerHTML = '<i class="fa-solid fa-pencil"></i>';
        editBtn.onclick = () => openCourseVariableModal(cv.id);
        const deleteBtn = document.createElement("button");
        deleteBtn.title = "Удалить";
        deleteBtn.className = "delete-btn";
        deleteBtn.innerHTML = '<i class="fa-regular fa-trash-can"></i>';
        deleteBtn.onclick = () => handleDeleteCourseVariable(cv.id, cv.variable_name);
        actionsDiv.appendChild(editBtn);
        actionsDiv.appendChild(deleteBtn);

        itemDiv.appendChild(nameSpan);
        itemDiv.appendChild(valueSpan);
        itemDiv.appendChild(actionsDiv);
        courseVariablesListDiv.appendChild(itemDiv);
      });
    } catch (error) {
      courseVariablesListDiv.innerHTML = `<p style="color:red;">Ошибка загрузки переменных.</p>`;
    }
  }

  function openCourseVariableModal(variableId = null) {
    cvModalError.textContent = "";
    editCvIdInput.value = "";
    const currentGcAccId = cvGcAccountSelect.value;
    if (!currentGcAccId) {
      alert(
        "Сначала выберите аккаунт GetCourse, для которого хотите добавить/изменить переменную.",
      );
      gcAccountStatus.textContent = "Выберите аккаунт GetCourse для переменных.";
      gcAccountStatus.style.color = "orange";
      return;
    }
    editCvGcAccountIdInput.value = currentGcAccId;

    if (variableId) {
      const cvData = currentCourseVariables.find((v) => v.id === variableId);
      if (!cvData) {
        console.error(`Переменная ${variableId} не найдена.`);
        return;
      }
      cvModalTitle.textContent = `Редактировать {{${cvData.variable_name}}}`;
      editCvIdInput.value = variableId;
      cvNameInput.value = cvData.variable_name;
      cvNameInput.disabled = true;
      cvValueInput.value = cvData.variable_value;
    } else {
      cvModalTitle.textContent = "Добавить Переменную Курса";
      cvNameInput.value = "";
      cvNameInput.disabled = false;
      cvValueInput.value = "";
    }
    courseVariableModal.classList.remove("hidden");
  }

  function closeCourseVariableModal() {
    courseVariableModal.classList.add("hidden");
  }

  async function handleSaveCourseVariable() {
    const variableId = editCvIdInput.value || null;
    const gcAccountId = editCvGcAccountIdInput.value;
    const name = cvNameInput.value.trim();
    const value = cvValueInput.value;

    if (!gcAccountId) {
      cvModalError.textContent = "Ошибка: ID аккаунта GetCourse не определен.";
      return;
    }
    if (!name || !/^[a-zA-Z0-9_]+$/.test(name)) {
      cvModalError.textContent =
        "Имя переменной обязательно и должно содержать только латинские буквы, цифры и подчеркивание.";
      return;
    }
    if (value === undefined || value === null) {
      cvModalError.textContent = "Значение переменной обязательно.";
      return;
    }
    cvModalError.textContent = "";
    courseVariableStatus.textContent = "Сохранение переменной...";
    courseVariableStatus.style.color = "orange";

    try {
      const payload = {
        getcourse_account_id: gcAccountId,
        variable_name: name,
        variable_value: value,
      };
      if (variableId) {
        payload.variable_id = variableId;
      }

      console.log("Popup: Sending action 'saveCourseVariable' with payload:", payload);
      await sendMessageToBackground("saveCourseVariable", payload);
      courseVariableStatus.textContent = "Переменная сохранена!";
      courseVariableStatus.style.color = "green";
      closeCourseVariableModal();
      delete courseVariablesCache[gcAccountId];
      await displayCourseVariables(gcAccountId, true);
      setTimeout(() => {
        if (courseVariableStatus.style.color === "var(--success-color)")
          courseVariableStatus.textContent = "";
      }, 3000);
    } catch (error) {
      cvModalError.textContent = `Ошибка: ${error.message}`;
    }
  }

  function openGcAccountModal(accountId = null) {
    gcModalError.textContent = "";
    editGcAccountIdInput.value = "";
    gcPasswordInput.setAttribute("required", "required");
    if (accountId) {
      const accountData = currentGcAccounts.find((acc) => acc.id === accountId);
      if (!accountData) {
        console.error(`Аккаунт ${accountId} не найден`);
        return;
      }
      gcModalTitle.textContent = `Редактировать (${accountData.subdomain})`;
      editGcAccountIdInput.value = accountId;
      gcSubdomainInput.value = accountData.subdomain;
      gcEmailInput.value = accountData.email;
      gcPasswordInput.value = "";
      gcPasswordInput.placeholder = "Оставьте пустым, чтобы не менять";
      gcPasswordInput.removeAttribute("required");
      gcIsActiveCheckbox.checked = accountData.is_active;
    } else {
      gcModalTitle.textContent = "Добавить аккаунт GetCourse";
      gcSubdomainInput.value = "";
      gcEmailInput.value = "";
      gcPasswordInput.value = "";
      gcPasswordInput.placeholder = "Введите пароль (обязательно)";
      gcIsActiveCheckbox.checked = true;
    }
    gcAccountModal.classList.remove("hidden");
  }
  function closeGcAccountModal() {
    if (gcAccountModal) gcAccountModal.classList.add("hidden");
  }
  async function handleSaveGcAccount() {
    const accountId = editGcAccountIdInput.value || null;
    const subdomain = gcSubdomainInput.value.trim();
    const email = gcEmailInput.value.trim();
    const password = gcPasswordInput.value;
    const isActive = gcIsActiveCheckbox.checked;
    if (!subdomain || !email) {
      gcModalError.textContent = "Поддомен и Email обязательны.";
      return;
    }
    if (!accountId && !password) {
      gcModalError.textContent = "Пароль обязателен при добавлении.";
      return;
    }
    const cleanSubdomain = subdomain
      .replace(/^(https?:\/\/)?(www\.)?/, "")
      .replace(/\.getcourse\.ru.*$/, "");
    if (!cleanSubdomain || cleanSubdomain.includes(".")) {
      gcModalError.textContent = 'Введите только поддомен (напр., "mycompany")';
      return;
    }
    gcModalError.textContent = "";
    gcAccountStatus.textContent = "Сохранение...";
    gcAccountStatus.style.color = "orange";
    try {
      const payload = { subdomain: cleanSubdomain, email: email, is_active: isActive };
      if (accountId) payload.accountId = accountId;
      if (password) payload.password = password;
      await sendMessageToBackground("saveGetCourseAccount", payload);
      gcAccountStatus.textContent = "Аккаунт сохранен! Статус обновится после проверки.";
      gcAccountStatus.style.color = "green";
      closeGcAccountModal();
      await displayGetCourseAccounts(true);
      setTimeout(() => {
        if (gcAccountStatus.style.color === "var(--success-color)")
          gcAccountStatus.textContent = "";
      }, 4000);
    } catch (error) {
      gcModalError.textContent = `Ошибка: ${error.message}`;
    }
  }
  async function handleDeleteGcAccount(accountId, subdomain) {
    if (!accountId) return;
    if (confirm(`Удалить аккаунт ${subdomain}.getcourse.ru?`)) {
      gcAccountStatus.textContent = "Удаление...";
      gcAccountStatus.style.color = "orange";
      try {
        await sendMessageToBackground("deleteGetCourseAccount", { accountId });
        gcAccountStatus.textContent = "Аккаунт удален.";
        gcAccountStatus.style.color = "green";
        gcAccountsCache = null;
        await displayGetCourseAccounts(true);
        setTimeout(() => {
          if (gcAccountStatus && gcAccountStatus.style.color === "var(--success-color)")
            gcAccountStatus.textContent = "";
        }, 2000);
      } catch (error) {}
    }
  }

  async function handleToggleActiveGcAccount(accountId, newActiveState) {
    if (!accountId) return;

    const actionText = newActiveState ? "Активация" : "Деактивация";
    gcAccountStatus.textContent = `${actionText} аккаунта...`;
    gcAccountStatus.style.color = "orange";

    const accountData = currentGcAccounts.find((acc) => acc.id === accountId);
    if (!accountData) {
      gcAccountStatus.textContent = "Ошибка: Аккаунт не найден для изменения статуса.";
      gcAccountStatus.style.color = "red";
      return;
    }

    try {
      const payload = {
        accountId: accountId,
        subdomain: accountData.subdomain,
        email: accountData.email,
        is_active: newActiveState,
      };

      await sendMessageToBackground("saveGetCourseAccount", payload);

      gcAccountStatus.textContent = `Аккаунт ${newActiveState ? "активирован" : "деактивирован"}.`;
      gcAccountStatus.style.color = "green";
      gcAccountsCache = null;
      await displayGetCourseAccounts(true);
      setTimeout(() => {
        if (gcAccountStatus.style.color === "var(--success-color)")
          gcAccountStatus.textContent = "";
      }, 3000);
    } catch (error) {
      gcAccountStatus.textContent = `Ошибка ${actionText.toLowerCase()}: ${error.message}`;
      gcAccountStatus.style.color = "red";
    }
  }

  async function handleDeleteCourseVariable(variableId, variableName) {
    if (!variableId) return;
    const currentGcAccId = cvGcAccountSelect.value;
    if (confirm(`Удалить переменную {{${variableName}}} для текущего аккаунта GetCourse?`)) {
      courseVariableStatus.textContent = "Удаление...";
      courseVariableStatus.style.color = "orange";
      try {
        await sendMessageToBackground("deleteCourseVariable", { variable_id: variableId });
        courseVariableStatus.textContent = "Переменная удалена.";
        courseVariableStatus.style.color = "green";
        const currentGcAccId = cvGcAccountSelect.value;
        delete courseVariablesCache[currentGcAccId];
        await displayCourseVariables(currentGcAccId, true);
        setTimeout(() => {
          if (courseVariableStatus.style.color === "var(--success-color)")
            courseVariableStatus.textContent = "";
        }, 2000);
      } catch (error) {}
    }
  }

  loginButton.addEventListener("click", handleLogin);
  signupButton.addEventListener("click", handleSignup);
  logoutButton.addEventListener("click", handleLogout);
  saveEditButton.addEventListener("click", handleSaveEdit);
  cancelEditButton.addEventListener("click", closeEditModal);
  addGcAccountButton.addEventListener("click", () => openGcAccountModal());
  saveGcAccountButton.addEventListener("click", handleSaveGcAccount);
  cancelGcAccountButton.addEventListener("click", closeGcAccountModal);
  if (cvGcAccountSelect)
    cvGcAccountSelect.addEventListener("change", (e) => displayCourseVariables(e.target.value));
  if (addCourseVariableButton)
    addCourseVariableButton.addEventListener("click", () => openCourseVariableModal());
  if (saveCourseVariableButton)
    saveCourseVariableButton.addEventListener("click", handleSaveCourseVariable);
  if (cancelCourseVariableButton)
    cancelCourseVariableButton.addEventListener("click", closeCourseVariableModal);

  checkSessionAndUpdateUI();
});
