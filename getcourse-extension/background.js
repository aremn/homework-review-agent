"use strict";
const { SUPABASE_URL } = require("./config");
const SESSION_STORAGE_KEY = "supabase.auth.token";

const storage = {
  getItem: async (key) => {
    try {
      const r = await chrome.storage.local.get(key);
      return r[key] ?? null;
    } catch (e) {
      console.error("Storage get error:", e);
      return null;
    }
  },
  setItem: async (key, value) => {
    try {
      return await chrome.storage.local.set({ [key]: value });
    } catch (e) {
      console.error("Storage set error:", e);
    }
  },
  removeItem: async (key) => {
    try {
      return await chrome.storage.local.remove(key);
    } catch (e) {
      console.error("Storage remove error:", e);
    }
  },
};

async function saveSessionTokens(session) {
  if (session && session.access_token && session.refresh_token) {
    const sessionData = { currentSession: session, expiresAt: session.expires_at };
    await storage.setItem(SESSION_STORAGE_KEY, JSON.stringify(sessionData));
    console.log("BG: Session tokens saved.");
  } else {
    console.warn("Invalid session; removing stored tokens.");
    await removeSessionTokens();
  }
}

async function getSessionTokens() {
  const stored = await storage.getItem(SESSION_STORAGE_KEY);
  if (stored) {
    try {
      const data = JSON.parse(stored);
      if (data?.currentSession?.access_token && data?.currentSession?.refresh_token) {
        return data.currentSession;
      }
    } catch (e) {
      console.error("BG: Error parsing stored session", e);
    }
  }
  return null;
}

async function removeSessionTokens() {
  await storage.removeItem(SESSION_STORAGE_KEY);
  console.log("BG: Session tokens removed.");
}

let isRefreshingToken = false;

async function callEdgeFunction(functionName, method = "GET", body = null, params = null) {
  let session = await getSessionTokens();
  let accessToken = session?.access_token;

  if (!accessToken && !["auth-signin", "auth-signup"].includes(functionName)) {
    throw new Error("User not authenticated (no access token)");
  }

  const makeRequest = async (token) => {
    let url = `${SUPABASE_URL}/functions/v1/${functionName}`;
    if (params && (method === "GET" || method === "DELETE")) {
      url += "?" + new URLSearchParams(params).toString();
    }
    let actualMethod = method;
    if (functionName === "manage-gc-account" && (method === "PUT" || method === "PATCH")) {
      actualMethod = "POST";
    }
    const options = {
      method: actualMethod,
      headers: { "Content-Type": "application/json" },
    };
    if (token && !["auth-signin", "auth-signup"].includes(functionName)) {
      options.headers["Authorization"] = `Bearer ${token}`;
    }

    if (body && ["POST", "PUT", "PATCH"].includes(actualMethod)) {
      options.body = JSON.stringify(body);
    }

    console.log(`BG: Calling EF: ${actualMethod} ${url} (token ${token ? "present" : "absent"})`);
    const response = await fetch(url, options);
    let responseBody = {};
    try {
      responseBody = await response.json();
    } catch (e) {
      responseBody = { error: `Invalid JSON (Status: ${response.status})` };
      if (response.ok) throw new Error(responseBody.error);
    }

    if (!response.ok) {
      console.error(`BG: EF '${functionName}' Error (${response.status}):`, responseBody);
      const isJwtError =
        response.status === 401 ||
        (responseBody.error && responseBody.error.toLowerCase().includes("jwt"));
      if (isJwtError && !["auth-signin", "auth-signup"].includes(functionName)) {
        console.warn(`BG: Access token possibly expired for protected function ${functionName}.`);
        const tokenExpiredError = new Error("Access token expired");
        tokenExpiredError.isTokenExpired = true;
        throw tokenExpiredError;
      } else {
        throw new Error(
          responseBody.error ||
            `Edge Function '${functionName}' failed with status ${response.status}`,
        );
      }
    }
    console.log(`BG: EF '${functionName}' Success:`, responseBody);
    return responseBody;
  };

  try {
    return await makeRequest(accessToken);
  } catch (error) {
    if (error.isTokenExpired && !isRefreshingToken) {
      isRefreshingToken = true;
      console.log("BG: Attempting token refresh...");
      const refreshToken = session?.refresh_token;
      if (!refreshToken) {
        isRefreshingToken = false;
        console.error("BG: No refresh token found.");
        await removeSessionTokens();
        throw new Error("Session expired, and no refresh token available.");
      }
      try {
        const refreshResponse = await callEdgeFunction("auth-refresh-token", "POST", {
          refresh_token: refreshToken,
        });
        if (refreshResponse.session) {
          console.log("BG: Token refresh successful.");
          await saveSessionTokens(refreshResponse.session);
          accessToken = refreshResponse.session.access_token;
          isRefreshingToken = false;
          console.log(`BG: Retrying original request to ${functionName}...`);
          return await makeRequest(accessToken);
        } else {
          isRefreshingToken = false;
          console.error("BG: Refresh call did not return session.");
          await removeSessionTokens();
          throw new Error("Refresh token call succeeded but returned no session.");
        }
      } catch (refreshError) {
        isRefreshingToken = false;
        console.error("BG: Token refresh failed:", refreshError);
        await removeSessionTokens();
        throw new Error(`Session refresh failed: ${refreshError.message}. Please log in again.`);
      }
    } else if (isRefreshingToken && error.isTokenExpired) {
      console.warn(`BG: Already refreshing token, ignoring error for ${functionName}.`);
      throw new Error("Token refresh in progress, please wait.");
    } else {
      throw error;
    }
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  console.log("BG Received:", message.action, message.payload || "");
  console.log(
    "BG Top-Level Received: Action=",
    message.action,
    "Payload=",
    JSON.stringify(message.payload, null, 2),
  );
  (async () => {
    try {
      let responseData = { success: true };
      if (message.action === "signUp") {
        const { email, password } = message.payload;
        const efResponse = await callEdgeFunction("auth-signup", "POST", { email, password });
        responseData.user = efResponse.user;
        responseData.session = efResponse.session;
      } else if (message.action === "signIn") {
        const { email, password } = message.payload;
        const efResponse = await callEdgeFunction("auth-signin", "POST", { email, password });
        if (efResponse.session) {
          await saveSessionTokens(efResponse.session);
        } else {
          console.warn("BG: No session from signIn EF.");
          await removeSessionTokens();
        }
        responseData.data = { session: efResponse.session, user: efResponse.user };
      } else if (message.action === "signOut") {
        try {
          await callEdgeFunction("auth-signout", "POST");
        } catch (e) {
          if (!e.message?.toLowerCase().includes("authenticated")) throw e;
          else console.log("BG: SignOut called when not authenticated.");
        }
        await removeSessionTokens();
      } else if (message.action === "getSession") {
        let session = await getSessionTokens();
        let user = null;
        if (session?.access_token) {
          try {
            const userResponse = await callEdgeFunction("auth-get-user", "GET");
            user = userResponse.user;
          } catch (getUserError) {
            console.warn("BG: getSession - getUser failed:", getUserError.message);
            if (
              getUserError.isTokenExpired ||
              getUserError.message?.toLowerCase().includes("jwt")
            ) {
              await removeSessionTokens();
              session = null;
            }
          }
        }
        responseData.session = session ? { ...session, user: user } : null;
      } else if (message.action === "savePrompt") {
        const { lessonId, promptText } = message.payload;
        const payloadForEF = { lesson_id: String(lessonId), prompt_text: promptText };
        responseData = await callEdgeFunction("save-prompt", "POST", message.payload);
      } else if (message.action === "loadAllPrompts") {
        responseData = await callEdgeFunction("get-prompts", "GET");
      } else if (message.action === "deletePrompt") {
        if (!message.payload.lessonId || message.payload.lessonId === "default")
          throw new Error("Invalid lessonId");
        responseData = await callEdgeFunction("delete-prompt", "DELETE", null, {
          lesson_id: message.payload.lessonId,
        });
      } else if (message.action === "listGetCourseAccounts") {
        responseData = await callEdgeFunction("get-gc-accounts", "GET");
      } else if (message.action === "saveGetCourseAccount") {
        const { accountId } = message.payload;

        responseData = await callEdgeFunction("manage-gc-account", "POST", message.payload);
      } else if (message.action === "deleteGetCourseAccount") {
        if (!message.payload.accountId) throw new Error("Account ID required");
        responseData = await callEdgeFunction("delete-gc-account", "DELETE", null, {
          account_id: message.payload.accountId,
        });
      } else if (message.action === "listCourseVariables") {
        if (!message.payload || !message.payload.getcourse_account_id) {
          throw new Error("Missing getcourse_account_id for listCourseVariables");
        }
        responseData = await callEdgeFunction("list-course-variables", "GET", null, {
          getcourse_account_id: message.payload.getcourse_account_id,
        });
      } else if (message.action === "saveCourseVariable") {
        const { variable_id, getcourse_account_id, variable_name, variable_value } =
          message.payload;
        if (!getcourse_account_id || !variable_name || variable_value === undefined) {
          throw new Error("Missing required fields for saveCourseVariable");
        }
        const method = variable_id ? "PUT" : "POST";
        responseData = await callEdgeFunction("save-course-variable", method, message.payload);
      } else if (message.action === "deleteCourseVariable") {
        if (!message.payload || !message.payload.variable_id) {
          throw new Error("Missing variable_id for deleteCourseVariable");
        }
        responseData = await callEdgeFunction("delete-course-variable", "DELETE", null, {
          variable_id: message.payload.variable_id,
        });
      } else if (message.action === "loadPrompt") {
        const { lessonId } = message.payload;
        console.log("BG loadPrompt: Received lessonId:", lessonId);
        try {
          const efResponse = await callEdgeFunction("get-prompt", "GET", null, {
            lesson_id: String(lessonId),
          });
          console.log("BG loadPrompt: EF Response:", efResponse);

          if (efResponse && efResponse.success && typeof efResponse.prompt_text === "string") {
            responseData.promptText = efResponse.prompt_text;
            responseData.notFound = false;
          } else {
            responseData.notFound = true;
            responseData.promptText = "";
            console.log(
              "BG loadPrompt: Prompt text NOT found in EF response or response unsuccessful.",
            );
          }
        } catch (error) {
          console.error("BG loadPrompt: Error during callEdgeFunction or processing:", error);
          if (
            error.message?.toLowerCase().includes("not found") ||
            error.message?.toLowerCase().includes("404")
          ) {
            responseData.notFound = true;
          } else {
            throw error;
          }
        }
      } else {
        console.warn("BG: Unknown action:", message.action);
        throw new Error("Unknown action");
      }

      if (responseData.success === undefined) responseData.success = true;
      sendResponse(responseData);
    } catch (error) {
      console.error(`BG Error (Action ${message.action}):`, error);
      sendResponse({ success: false, error: error.message || String(error) });
    }
  })();
  return true;
});

console.log("Background script (token-managed, no-client) loaded.");
