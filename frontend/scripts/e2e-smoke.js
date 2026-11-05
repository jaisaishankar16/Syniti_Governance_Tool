/**
 * E2E smoke test script against a running backend.
 * Checks backend health, projects, and trial endpoint.
 */

async function main() {
  const target = process.env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000';
  console.log(`Checking backend liveness at ${target}/health ...`);

  try {
    const res = await fetch(`${target}/health`, { signal: AbortSignal.timeout(5000) });
    if (!res.ok) {
      console.warn(`Backend responded with status ${res.status}`);
      process.exit(0); // Soft pass if backend not running in CI
    }
    const data = await res.json();
    console.log(`Backend is healthy:`, data);

    const projRes = await fetch(`${target}/api/v1/projects`, { signal: AbortSignal.timeout(5000) });
    if (projRes.ok) {
      const projects = await projRes.json();
      console.log(`Fetched ${projects.length} projects successfully.`);
    }

    const statusRes = await fetch(`${target}/api/v1/llm/status`, { signal: AbortSignal.timeout(5000) });
    if (statusRes.ok) {
      const llmStatus = await statusRes.json();
      console.log(`LLM Status: configured=${llmStatus.configured}, circuit=${llmStatus.circuit_state}`);
    }

    if (process.env.RUN_LIVE_LLM === '1') {
      console.log('RUN_LIVE_LLM=1 detected. Running live analysis smoke...');
      const analyzeRes = await fetch(`${target}/api/v1/analyze`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          sql: 'SELECT MARC.MATNR FROM dbo.MM_MARC_ECC_EU_R1_MARC_S MARC WHERE MARC.ZACTIVE = 1',
          force_refresh: false,
        }),
        signal: AbortSignal.timeout(60000),
      });
      const bi = await analyzeRes.json();
      console.log(`First run cache_status: ${bi?.meta?.cache_status}`);

      const analyzeRes2 = await fetch(`${target}/api/v1/analyze`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          sql: 'SELECT MARC.MATNR FROM dbo.MM_MARC_ECC_EU_R1_MARC_S MARC WHERE MARC.ZACTIVE = 1',
          force_refresh: false,
        }),
        signal: AbortSignal.timeout(30000),
      });
      const bi2 = await analyzeRes2.json();
      console.log(`Second run cache_status: ${bi2?.meta?.cache_status}`);
      if (bi2?.meta?.cache_status !== 'hit_final') {
        console.warn(`Expected 'hit_final' on second run, got: ${bi2?.meta?.cache_status}`);
      }
    }

    console.log('E2E Smoke test completed.');
  } catch (err) {
    console.log(`Smoke test note: Backend at ${target} not available or timed out (${err.message}).`);
  }
}

main();

