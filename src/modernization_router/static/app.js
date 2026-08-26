const form = document.querySelector("#modernize-form");
const code = document.querySelector("#source-code");
const file = document.querySelector("#code-file");
const resultPanel = document.querySelector("#result-panel");
const resultContent = document.querySelector("#result-content");
const statusLine = document.querySelector("#status-line");
const usage = document.querySelector("#usage");
const runButton = document.querySelector("#run-button");

function promptFor(task, source, target, instructions, sourceCode) {
  const goals = {
    refactoring: `Convert this ${source} code to ${target}. Preserve observable behavior. Return complete code followed by concise migration notes.`,
    code_analysis: `Analyze this ${source} code. Identify behavior, dependencies, risks, and the smallest safe modernization path toward ${target}.`,
    debugging: `Debug this ${source} code. Find the root cause, provide the smallest correct fix, and explain how to verify it.`,
    test_generation: `Generate focused tests for this ${source} code. Cover important behavior and failure paths without unnecessary scaffolding.`,
    architecture: `Design a practical modernization plan from ${source} toward ${target}. Prioritize incremental delivery, compatibility, and risk control.`,
  };
  return `${goals[task]}\n\nAdditional instructions: ${instructions || "None"}\n\nSOURCE CODE:\n${sourceCode}`;
}

code.addEventListener("input", () => {
  document.querySelector("#character-count").textContent = `${code.value.length.toLocaleString()} / 50,000`;
});

file.addEventListener("change", async () => {
  const selected = file.files[0];
  if (!selected) return;
  if (selected.size > 100_000) {
    alert("For this beta, please use a text file smaller than 100 KB.");
    file.value = "";
    return;
  }
  code.value = (await selected.text()).slice(0, 50_000);
  code.dispatchEvent(new Event("input"));
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const sourceCode = code.value.trim();
  if (!sourceCode) return code.focus();

  const accessKey = document.querySelector("#access-key").value;
  const task = document.querySelector("#task").value;
  const prompt = promptFor(
    task,
    document.querySelector("#source-language").value,
    document.querySelector("#target-language").value,
    document.querySelector("#requirements").value.trim(),
    sourceCode,
  );

  runButton.disabled = true;
  runButton.firstChild.textContent = "Routing job… ";
  resultPanel.hidden = false;
  statusLine.textContent = "Choosing an eligible low-cost model…";
  resultContent.textContent = "";
  usage.replaceChildren();
  resultPanel.scrollIntoView({ behavior: "smooth", block: "start" });

  try {
    const response = await fetch("/v1/route", {
      method: "POST",
      headers: { "Authorization": `Bearer ${accessKey}`, "Content-Type": "application/json" },
      body: JSON.stringify({
        task,
        messages: [
          { role: "system", content: "You are a careful legacy-code modernization engineer. Be concise, preserve behavior, flag uncertainty, and never invent missing requirements." },
          { role: "user", content: prompt },
        ],
        privacy: "public",
        allow_premium_fallback: false,
        estimated_input_tokens: Math.max(1, Math.ceil(prompt.length / 4)),
        max_output_tokens: 4000,
        metadata: { surface: "public_beta" },
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "The conversion could not be completed.");

    statusLine.textContent = `Completed with ${payload.model_id}`;
    resultContent.textContent = payload.content;
    const facts = [
      `${payload.usage.total_tokens.toLocaleString()} tokens`,
      `$${payload.usage.cost_usd.toFixed(6)}`,
      `${payload.attempts.length} attempt${payload.attempts.length === 1 ? "" : "s"}`,
    ];
    facts.forEach((fact) => {
      const item = document.createElement("span");
      item.textContent = fact;
      usage.append(item);
    });
  } catch (error) {
    statusLine.textContent = "Job stopped safely";
    resultContent.textContent = error.message;
  } finally {
    runButton.disabled = false;
    runButton.firstChild.textContent = "Run modernization ";
  }
});

document.querySelector("#copy-result").addEventListener("click", async () => {
  await navigator.clipboard.writeText(resultContent.textContent);
});

document.querySelector("#download-result").addEventListener("click", () => {
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([resultContent.textContent], { type: "text/markdown" }));
  link.download = "modernization-result.md";
  link.click();
  URL.revokeObjectURL(link.href);
});
