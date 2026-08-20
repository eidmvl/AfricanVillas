const form = document.querySelector("#login-form");
const errorBox = document.querySelector("#login-error");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorBox.textContent = "";
  const button = form.querySelector("button");
  button.disabled = true;
  button.textContent = "Проверяем…";
  try {
    const payload = Object.fromEntries(new FormData(form).entries());
    const response = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.detail || "Не удалось войти");
    window.location.assign("/");
  } catch (error) {
    errorBox.textContent = error.message;
    button.disabled = false;
    button.textContent = "Войти";
  }
});
