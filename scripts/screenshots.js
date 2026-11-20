// Dashboard screenshots for the README: node scripts/screenshots.js http://127.0.0.1:8000 docs/screenshots
const { chromium } = require("playwright");
(async () => {
  const base = process.argv[2] || "http://127.0.0.1:8000";
  const out = process.argv[3] || "docs/screenshots";
  const runAdvisor = process.argv[4] !== "skip-advisor";
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 2 });
  page.on("pageerror", (e) => console.log("pageerror", e.message));
  await page.goto(base);
  await page.waitForTimeout(2500);
  await page.screenshot({ path: `${out}/01_process_map.png` });
  await page.click("#tabs button[data-tab=findings]");
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${out}/02_findings.png`, fullPage: true });
  await page.click("#tabs button[data-tab=levers]");
  await page.waitForTimeout(5000);
  await page.screenshot({ path: `${out}/03_levers.png`, fullPage: true });
  await page.click("#tabs button[data-tab=whatif]");
  await page.click("#btnWhatIf");
  await page.waitForTimeout(3000);
  await page.screenshot({ path: `${out}/04_whatif.png`, fullPage: true });
  await page.click("#tabs button[data-tab=advisor]");
  await page.fill("#userName", "Lazar");
  if (runAdvisor) {
    await page.click("#btnAdvise");
    await page.waitForFunction(() => !document.querySelector("#btnAdvise").disabled, null, { timeout: 300000 });
    await page.waitForTimeout(800);
    const acc = await page.$("button[data-acc]");
    if (acc) { await acc.click(); await page.waitForTimeout(800); }
  }
  await page.screenshot({ path: `${out}/05_advisor.png`, fullPage: true });
  await browser.close();
})();
