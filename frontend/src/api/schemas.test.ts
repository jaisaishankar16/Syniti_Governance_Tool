import { describe, it, expect } from 'vitest';
import type { z } from 'zod';
import type { components } from './schema.gen';
import {
  HealthResponseSchema,
  ReadyResponseSchema,
  PrincipalSchema,
  ProjectSchema,
  ActivityItemSchema,
  ExampleQueryItemSchema,
  LLMStatusResponseSchema,
  CacheStatsSchema,
  CacheClearResponseSchema,
  AdminPingResponseSchema,
  LearnedSapEntrySchema,
  BusinessIntelligenceSchema,
  LlmSettingsViewSchema,
  type HealthResponse,
  type ReadyResponse,
  type Project,
  type ActivityItem,
  type ExampleQueryItem,
  type LLMStatusResponse,
  type CacheStats,
  type CacheClearResponse,
  type AdminPingResponse,
  type LearnedSapEntry,
  type LlmSettingsView,
} from './schemas';

// ==============================================================================
// Type-level assertions: Zod schemas agree with schema.gen.ts
// ==============================================================================

type GenHealth = components['schemas']['HealthResponse'];
type GenReady = components['schemas']['ReadyResponse'];
type GenPrincipal = components['schemas']['Principal'];
type GenProject = components['schemas']['Project'];
type GenActivity = components['schemas']['ActivityItem'];
type GenExample = components['schemas']['ExampleQueryItem'];
type GenLLMStatus = components['schemas']['LLMStatusResponse'];
type GenCacheStats = components['schemas']['CacheStats'];
type GenCacheClear = components['schemas']['CacheClearResponse'];
type GenAdminPing = components['schemas']['AdminPingResponse'];
type GenLearnedSap = components['schemas']['LearnedSapEntry'];
type GenBI = components['schemas']['BusinessIntelligence'];
type GenLlmSettings = components['schemas']['LlmSettingsView'];

// Type-level test helpers
type Assignable<A, B> = [A] extends [B] ? true : false;

function checkType<T extends true>(_value?: T): void {}

// 1. Inbound server payload compatibility (Gen -> Zod Input)
checkType<Assignable<GenHealth, z.input<typeof HealthResponseSchema>>>();
checkType<Assignable<GenReady, z.input<typeof ReadyResponseSchema>>>();
checkType<Assignable<GenPrincipal, z.input<typeof PrincipalSchema>>>();
checkType<Assignable<GenProject, z.input<typeof ProjectSchema>>>();
checkType<Assignable<GenActivity, z.input<typeof ActivityItemSchema>>>();
checkType<Assignable<GenExample, z.input<typeof ExampleQueryItemSchema>>>();
checkType<Assignable<GenLLMStatus, z.input<typeof LLMStatusResponseSchema>>>();
checkType<Assignable<GenCacheStats, z.input<typeof CacheStatsSchema>>>();
checkType<Assignable<GenCacheClear, z.input<typeof CacheClearResponseSchema>>>();
checkType<Assignable<GenAdminPing, z.input<typeof AdminPingResponseSchema>>>();
checkType<Assignable<GenLearnedSap, z.input<typeof LearnedSapEntrySchema>>>();
checkType<Assignable<GenBI, z.input<typeof BusinessIntelligenceSchema>>>();
checkType<Assignable<GenLlmSettings, z.input<typeof LlmSettingsViewSchema>>>();

// 2. Outbound inferred type compatibility (Zod Inferred -> Gen)
checkType<Assignable<HealthResponse, GenHealth>>();
checkType<Assignable<ReadyResponse, GenReady>>();
checkType<Assignable<Project, GenProject>>();
checkType<Assignable<ActivityItem, GenActivity>>();
checkType<Assignable<ExampleQueryItem, GenExample>>();
checkType<Assignable<LLMStatusResponse, GenLLMStatus>>();
checkType<Assignable<CacheStats, GenCacheStats>>();
checkType<Assignable<CacheClearResponse, GenCacheClear>>();
checkType<Assignable<AdminPingResponse, GenAdminPing>>();
checkType<Assignable<LearnedSapEntry, GenLearnedSap>>();
checkType<Assignable<LlmSettingsView, GenLlmSettings>>();

describe('Zod API Schemas runtime validation', () => {
  it('validates a valid Project payload', () => {
    const raw = {
      id: 'proj-1',
      name: 'SAP S/4HANA Migration',
      description: 'Material master migration wave 1',
      status: 'active',
      release_count: 3,
      milestone_count: 12,
    };
    const parsed = ProjectSchema.parse(raw);
    expect(parsed.name).toBe('SAP S/4HANA Migration');
  });

  it('validates an ActivityItem payload', () => {
    const raw = {
      id: 'act-1',
      status: 'complete',
      run_type: 'Initial Validation',
      table_name: 'MARA',
      subtitle: 'Material Master General Data',
      project_id: 'proj-1',
      finished_at: '2026-11-05T10:00:00Z',
    };
    const parsed = ActivityItemSchema.parse(raw);
    expect(parsed.status).toBe('complete');
  });

  it('validates LLMStatusResponse', () => {
    const raw = {
      configured: true,
      model: 'gpt-4o-mini',
      api_mode: 'responses',
      base_url_set: false,
      circuit_state: 'CLOSED',
      budget: {
        used_tokens: 12500,
        daily_limit: 100000,
      },
      last_error_code: null,
      last_error_at: null,
    };
    const parsed = LLMStatusResponseSchema.parse(raw);
    expect(parsed.configured).toBe(true);
    expect(parsed.circuit_state).toBe('CLOSED');
  });

  it('validates a complete BusinessIntelligence payload', () => {
    const raw = {
      business_meaning: 'Validates active materials in plant data',
      technical_summary: 'Selects records from MARC and MARA',
      affected_entities: ['MARC', 'MARA'],
      sap_module: 'MM',
      query_type: 'Data Quality Filter',
      risk_summary: {
        level: 'low',
        items: ['None identified'],
        recommendations: ['Maintain existing indexing'],
      },
      final_learning: null,
      governance: {
        subject_area: 'Supply Chain',
        business_process: 'Procure to Pay',
        dataset: 'Material Plant Data',
        business_concept: {
          term: 'Active Material Plant Status',
          field: 'ZACTIVE',
          description: 'Indicator of active material record',
        },
        rule: 'Material Must Be Active',
        specification: 'ZACTIVE must be 1 for MARC entries',
        enforcement: 'Block migration of inactive records',
        implementation: 'MARC.ZACTIVE = 1',
        opportunity_definition: 'All MARC records',
        error_definition: 'MARC records where ZACTIVE <> 1',
        opportunity_query: 'SELECT MATNR FROM dbo.MARC',
        error_query: 'SELECT MATNR FROM dbo.MARC WHERE ZACTIVE <> 1',
        is_new_flags: {
          subject_area: false,
          business_process: false,
          business_concept: true,
        },
      },
      meta: {
        request_id: 'req-12345',
        cache_status: 'miss',
        cache_detail: {
          sql_agent: 'miss',
          sap_agent: 'miss',
        },
        similarity_score: null,
        model: 'gpt-4o-mini',
        prompt_versions: {
          sql: 'v1.0.0',
          sap: 'v1.0.0',
          synthesizer: 'v1.0.0',
        },
        agent_timings_ms: { sql: 120, sap: 140 },
        stage_timings_ms: { total: 450 },
        token_usage: { sql: 500, sap: 400, synthesizer: 600, total: 1500 },
        estimated_cost_usd: 0.002,
        degraded: false,
        degraded_reasons: [],
        warnings: [],
        guardrails: {
          events: [
            {
              id: 'ev-1',
              name: 'sql_syntax_check',
              stage: 'input',
              action: 'pass',
              severity: 'info',
              message: 'SQL syntax is valid',
              details: [{ key: 'parser', value: 'tsql' }],
              timestamp: '2026-11-05T10:00:00Z',
            },
          ],
          blocked: false,
          counts_by_action: { pass: 1 },
        },
        validation: {
          opportunity_sql_ok: true,
          error_sql_ok: true,
          subset_ok: true,
          glossary_ok: true,
          matches_parser_baseline: true,
        },
        ai_generated: true,
        parse_quality: 'full',
        naming_violations: [],
        cached_at: null,
      },
    };

    const parsed = BusinessIntelligenceSchema.parse(raw);
    expect(parsed.meta.request_id).toBe('req-12345');
    expect(parsed.governance.is_new_flags.business_concept).toBe(true);
  });
});
