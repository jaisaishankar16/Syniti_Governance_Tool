import { z } from 'zod';

// ==============================================================================
// Health & Readiness
// ==============================================================================

export const HealthResponseSchema = z.object({
  status: z.string(),
  timestamp: z.string(),
});
export type HealthResponse = z.infer<typeof HealthResponseSchema>;

export const ReadyResponseSchema = z.object({
  status: z.enum(['ready', 'not_ready']),
  checks: z.record(z.string(), z.boolean()),
});
export type ReadyResponse = z.infer<typeof ReadyResponseSchema>;

// ==============================================================================
// User Identity & Navigation
// ==============================================================================

export const PrincipalSchema = z.object({
  id: z.string(),
  display_name: z.string(),
  email: z.string(),
  roles: z.array(z.string()).optional().default([]),
  initials: z.string().optional().default('AU'),
});
export type Principal = z.infer<typeof PrincipalSchema>;

// ==============================================================================
// Projects & Activity
// ==============================================================================

export const ProjectSchema = z.object({
  id: z.string(),
  name: z.string(),
  description: z.string(),
  status: z.enum(['active', 'archived']),
  release_count: z.number().int(),
  milestone_count: z.number().int(),
});
export type Project = z.infer<typeof ProjectSchema>;
export const ProjectsListSchema = z.array(ProjectSchema);

export const ProjectCreateRequestSchema = z.object({
  name: z.string().min(1).max(80),
  description: z.string().max(300).nullable().optional(),
});
export type ProjectCreateRequest = z.infer<typeof ProjectCreateRequestSchema>;

export const MetadataRefreshRequestSchema = z.object({
  skp_url: z.string().min(1).max(2048),
});
export type MetadataRefreshRequest = z.infer<typeof MetadataRefreshRequestSchema>;

export const ActivityItemSchema = z.object({
  id: z.string(),
  request_id: z.string().nullable().optional(),
  status: z.enum(['complete', 'degraded', 'failed']),
  run_type: z.string(),
  table_name: z.string(),
  subtitle: z.string(),
  project_id: z.string().nullable().optional(),
  cache_status: z.string().nullable().optional(),
  duration_ms: z.number().nullable().optional(),
  finished_at: z.string(),
});
export type ActivityItem = z.infer<typeof ActivityItemSchema>;
export const ActivityListSchema = z.array(ActivityItemSchema);

// ==============================================================================
// Examples & LLM Status
// ==============================================================================

export const ExampleQueryItemSchema = z.object({
  id: z.string(),
  title: z.string(),
  sql: z.string(),
});
export type ExampleQueryItem = z.infer<typeof ExampleQueryItemSchema>;
export const ExampleQueryListSchema = z.array(ExampleQueryItemSchema);

export const LLMBudgetStatusSchema = z.object({
  used_tokens: z.number().int(),
  daily_limit: z.number().int(),
});
export type LLMBudgetStatus = z.infer<typeof LLMBudgetStatusSchema>;

export const LLMStatusResponseSchema = z.object({
  configured: z.boolean(),
  model: z.string(),
  api_mode: z.string(),
  base_url_set: z.boolean(),
  circuit_state: z.string(),
  budget: LLMBudgetStatusSchema,
  last_error_code: z.string().nullable().optional(),
  last_error_at: z.string().nullable().optional(),
});
export type LLMStatusResponse = z.infer<typeof LLMStatusResponseSchema>;

export const LlmSettingsViewSchema = z.object({
  model: z.string(),
  api_mode: z.string(),
  structured_output_mode: z.string(),
  temperature: z.number(),
  max_turns: z.number().int(),
  timeout_s: z.number(),
  max_retries: z.number().int(),
  provider: z.string(),
  base_url: z.string(),
  api_key_configured: z.boolean(),
  learning_enabled: z.boolean(),
  tracing_enabled: z.boolean(),
  token_budgets: z.record(z.string(), z.unknown()),
  circuit_state: z.string(),
  last_error_code: z.string().nullable().optional(),
  last_error_at: z.string().nullable().optional(),
  prompt_versions: z.record(z.string(), z.string()),
  embedding_model: z.string(),
  knowledge_index_status: z.record(z.string(), z.unknown()),
  cache_backend: z.record(z.string(), z.unknown()),
  guardrail_modes: z.record(z.string(), z.unknown()),
  pricing_configured: z.boolean(),
});
export type LlmSettingsView = z.infer<typeof LlmSettingsViewSchema>;

// ==============================================================================
// Governance & Synthesis
// ==============================================================================

export const RiskSummarySchema = z.object({
  level: z.enum(['low', 'medium', 'high', 'critical']),
  items: z.array(z.string()).optional().default([]),
  recommendations: z.array(z.string()).optional().default([]),
});
export type RiskSummary = z.infer<typeof RiskSummarySchema>;

export const BusinessConceptSchema = z.object({
  term: z.string(),
  field: z.string().nullable().optional(),
  description: z.string(),
});
export type BusinessConcept = z.infer<typeof BusinessConceptSchema>;

export const IsNewFlagsSchema = z.object({
  subject_area: z.boolean().optional().default(false),
  business_process: z.boolean().optional().default(false),
  business_concept: z.boolean().optional().default(false),
});
export type IsNewFlags = z.infer<typeof IsNewFlagsSchema>;

export const GovernanceOutputSchema = z.object({
  subject_area: z.string(),
  business_process: z.string(),
  dataset: z.string(),
  business_concept: BusinessConceptSchema,
  rule: z.string(),
  specification: z.string(),
  enforcement: z.string(),
  implementation: z.string(),
  opportunity_definition: z.string(),
  error_definition: z.string(),
  opportunity_query: z.string(),
  error_query: z.string(),
  is_new_flags: IsNewFlagsSchema.optional().default({
    subject_area: false,
    business_process: false,
    business_concept: false,
  }),
});
export type GovernanceOutput = z.infer<typeof GovernanceOutputSchema>;

// ==============================================================================
// Metadata, Guardrails & Execution
// ==============================================================================

export const CacheDetailSchema = z.object({
  sql_agent: z
    .enum(['hit', 'miss', 'similar_reused', 'similar_hint', 'skipped'])
    .optional()
    .default('miss'),
  sap_agent: z
    .enum(['hit', 'miss', 'similar_reused', 'similar_hint', 'skipped'])
    .optional()
    .default('miss'),
});
export type CacheDetail = z.infer<typeof CacheDetailSchema>;

export const PromptVersionsSchema = z.object({
  sql: z.string(),
  sap: z.string(),
  synthesizer: z.string(),
});
export type PromptVersions = z.infer<typeof PromptVersionsSchema>;

export const TokenUsageBreakdownSchema = z.object({
  sql: z.number().int().optional().default(0),
  sap: z.number().int().optional().default(0),
  synthesizer: z.number().int().optional().default(0),
  total: z.number().int().optional().default(0),
});
export type TokenUsageBreakdown = z.infer<typeof TokenUsageBreakdownSchema>;

export const ValidationSummarySchema = z.object({
  opportunity_sql_ok: z.boolean(),
  error_sql_ok: z.boolean(),
  subset_ok: z.boolean(),
  glossary_ok: z.boolean(),
  matches_parser_baseline: z.boolean().optional().default(true),
});
export type ValidationSummary = z.infer<typeof ValidationSummarySchema>;

export const KeyValueSchema = z.object({
  key: z.string(),
  value: z.string(),
});
export type KeyValue = z.infer<typeof KeyValueSchema>;

export const GuardrailEventSchema = z.object({
  id: z.string().optional().default(''),
  name: z.string(),
  stage: z.enum(['input', 'tool', 'agent_output', 'final_output', 'budget']),
  action: z.enum(['pass', 'warn', 'sanitize', 'repair', 'block']),
  severity: z.enum(['info', 'low', 'medium', 'high', 'critical']),
  message: z.string(),
  details: z.array(KeyValueSchema).optional().default([]),
  agent: z.string().nullable().optional(),
  request_id: z.string().nullable().optional(),
  timestamp: z.string().optional().default(''),
});
export type GuardrailEvent = z.infer<typeof GuardrailEventSchema>;

export const GuardrailReportSchema = z.object({
  events: z.array(GuardrailEventSchema).optional().default([]),
  blocked: z.boolean().optional().default(false),
  highest_severity: z.enum(['info', 'low', 'medium', 'high', 'critical']).nullable().optional(),
  counts_by_action: z.record(z.string(), z.number()).optional().default({}),
});
export type GuardrailReport = z.infer<typeof GuardrailReportSchema>;

export const BusinessIntelligenceMetaSchema = z.object({
  request_id: z.string(),
  cache_status: z.enum(['hit_final', 'partial', 'miss', 'refresh']),
  cache_detail: CacheDetailSchema,
  similarity_score: z.number().nullable().optional(),
  model: z.string(),
  prompt_versions: PromptVersionsSchema,
  agent_timings_ms: z.record(z.string(), z.number()).optional().default({}),
  stage_timings_ms: z.record(z.string(), z.number()).optional().default({}),
  token_usage: TokenUsageBreakdownSchema.optional().default({
    sql: 0,
    sap: 0,
    synthesizer: 0,
    total: 0,
  }),
  estimated_cost_usd: z.number().nullable().optional(),
  degraded: z.boolean(),
  degraded_reasons: z.array(z.string()).optional().default([]),
  warnings: z.array(z.string()).optional().default([]),
  guardrails: GuardrailReportSchema.optional().default({
    events: [],
    blocked: false,
    counts_by_action: {},
  }),
  validation: ValidationSummarySchema,
  ai_generated: z.boolean(),
  parse_quality: z.string(),
  naming_violations: z.array(z.string()).optional().default([]),
  cached_at: z.string().nullable().optional(),
});
export type BusinessIntelligenceMeta = z.infer<typeof BusinessIntelligenceMetaSchema>;

export const BusinessIntelligenceSchema = z.object({
  business_meaning: z.string(),
  technical_summary: z.string(),
  affected_entities: z.array(z.string()),
  sap_module: z.string(),
  query_type: z.string(),
  risk_summary: RiskSummarySchema,
  final_learning: z.string().nullable().optional(),
  governance: GovernanceOutputSchema,
  meta: BusinessIntelligenceMetaSchema,
});
export type BusinessIntelligence = z.infer<typeof BusinessIntelligenceSchema>;

// ==============================================================================
// Admin Schemas
// ==============================================================================

export const CacheStatsSchema = z.object({
  total_requests: z.number().int().optional().default(0),
  l1_hits: z.number().int().optional().default(0),
  l1_misses: z.number().int().optional().default(0),
  l1_hit_ratio: z.number().optional().default(0.0),
  l2_sql_hits: z.number().int().optional().default(0),
  l2_sql_misses: z.number().int().optional().default(0),
  l2_sap_hits: z.number().int().optional().default(0),
  l2_sap_misses: z.number().int().optional().default(0),
  l3_similarity_reuses: z.number().int().optional().default(0),
  l3_similarity_hints: z.number().int().optional().default(0),
  entries_count: z.number().int().optional().default(0),
  backend_type: z.string().optional().default('memory'),
  backend_healthy: z.boolean().optional().default(true),
  estimated_tokens_saved: z.number().int().optional().default(0),
  estimated_cost_saved_usd: z.number().optional().default(0.0),
});
export type CacheStats = z.infer<typeof CacheStatsSchema>;

export const CacheClearResponseSchema = z.object({
  cleared: z.boolean(),
  kind: z.string(),
});
export type CacheClearResponse = z.infer<typeof CacheClearResponseSchema>;

export const AdminPingResponseSchema = z.object({
  status: z.enum(['ok', '401', '429', 'network_error']),
  latency_ms: z.number(),
  message: z.string(),
});
export type AdminPingResponse = z.infer<typeof AdminPingResponseSchema>;

export const EvidenceSchema = z.object({
  chunk_id: z.string(),
  source_file: z.string(),
  page: z.number().int().nullable().optional(),
  evidence_text: z.string(),
  retrieval_score: z.number(),
  match_type: z.enum(['exact', 'semantic']),
});
export type Evidence = z.infer<typeof EvidenceSchema>;

export const LearnedSapEntrySchema = z.object({
  id: z.string().nullable().optional(),
  table: z.string(),
  field: z.string().nullable().optional(),
  description: z.string(),
  business_meaning: z.string().nullable().optional(),
  module: z.string().nullable().optional(),
  evidence: EvidenceSchema,
  confidence: z.number().min(0).max(1),
  source: z.literal('retrieved').optional().default('retrieved'),
  status: z.enum(['pending', 'approved', 'rejected']).optional().default('pending'),
  created_at: z.string(),
  last_seen_at: z.string(),
  hit_count: z.number().int().optional().default(1),
});
export type LearnedSapEntry = z.infer<typeof LearnedSapEntrySchema>;
export const LearnedSapListSchema = z.array(LearnedSapEntrySchema);

export const GlossaryProposalSchema = z.record(z.string(), z.unknown());
export const GlossaryProposalsListSchema = z.array(GlossaryProposalSchema);

export const AdminActionResponseSchema = z.object({
  id: z.string(),
  status: z.string(),
  auditor: z.string(),
});
export type AdminActionResponse = z.infer<typeof AdminActionResponseSchema>;

// ==============================================================================
// Error Envelope & SSE Progress
// ==============================================================================

export const ErrorDetailSchema = z.object({
  field: z.string().nullable().optional(),
  issue: z.string(),
});
export type ErrorDetail = z.infer<typeof ErrorDetailSchema>;

export const ErrorEnvelopeBodySchema = z.object({
  code: z.string(),
  message: z.string(),
  request_id: z.string(),
  retry_after_s: z.number().int().nullable().optional(),
  details: z.array(ErrorDetailSchema).optional().default([]),
});
export type ErrorEnvelopeBody = z.infer<typeof ErrorEnvelopeBodySchema>;

export const ErrorEnvelopeSchema = z.object({
  error: ErrorEnvelopeBodySchema,
});
export type ErrorEnvelope = z.infer<typeof ErrorEnvelopeSchema>;

export const ProgressEventStageSchema = z.enum([
  'guardrails',
  'parsing',
  'cache_lookup',
  'agents_running',
  'synthesizing',
  'validating',
  'done',
  'error',
]);
export type ProgressEventStage = z.infer<typeof ProgressEventStageSchema>;

export const ProgressEventSchema = z.object({
  stage: ProgressEventStageSchema,
  message: z.string(),
  elapsed_ms: z.number(),
  detail: z.record(z.string(), z.unknown()).optional().default({}),
});
export type ProgressEvent = z.infer<typeof ProgressEventSchema>;
