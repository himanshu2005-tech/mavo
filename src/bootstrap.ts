// No application imports here: even a broken dependency must leave a usable startup screen.
const startupTimer = window.setTimeout(() => showStartupError(), 25_000);

function showStartupError() {
  const startup = document.getElementById("mavo-startup");
  if (!startup) return; // React already mounted; its error boundary owns later failures.
  const card = startup.querySelector(".startup-card");
  if (!card) return;
  const heading = document.createElement("h1");
  heading.textContent = "Mavo couldn’t start";
  const message = document.createElement("p");
  message.setAttribute("role", "alert");
  message.textContent = "Your saved data is safe. Reload Mavo to try again, or restart the app if this continues.";
  const reload = document.createElement("button");
  reload.textContent = "Reload Mavo";
  reload.addEventListener("click", () => window.location.reload());
  card.replaceChildren(heading, message, reload);
}

void import("./main").catch(() => showStartupError()).finally(() => window.clearTimeout(startupTimer));
