import React from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Cpu,
  RefreshCw,
  CheckCircle2,
  AlertTriangle,
  Server,
  Shield,
  Layers,
  FileCode,
  DollarSign,
  Database,
  Info,
  AlertCircle,
} from 'lucide-react';
import { PageHeader } from '@/components/PageHeader';
import { apiFetch } from '@/api/client';
import { LlmSettingsViewSchema, type LlmSettingsView } from '@/api/schemas';
import { cn } from '@/lib/utils';

export const LlmSettingsPage: React.FC = () => {
  const {
    data: settings,
    isLoading,
    isError,
    error,
    refetch,
    dataUpdatedAt,
  } = useQuery<LlmSettingsView>({
    queryKey: ['llm-settings-view'],
    queryFn: () =>
      apiFetch<LlmSettingsView>('/api/v1/llm/settings', { schema: LlmSettingsViewSchema }),
    staleTime: 30_000,
  });

  const lastUpdatedStr = dataUpdatedAt ? new Date(dataUpdatedAt).toLocaleTimeString() : 'Never';

  return (
    <div className="space-y-8 pb-16">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <PageHeader
          title="LLM Settings & Diagnostics"
          subtitle="Read-only runtime telemetry, guardrail limits, and model configuration"
        />

        <div className="flex items-center space-x-3 self-start sm:self-auto">
          <span className="text-xs text-[#6B7280]">
            Last updated: <strong className="text-[#111827]">{lastUpdatedStr}</strong>
          </span>
          <button
            type="button"
            onClick={() => refetch()}
            disabled={isLoading}
            className="inline-flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-[#111827] bg-white border border-[#E5E7EB] hover:bg-gray-50 focus-visible:ring-2 focus-visible:ring-[#6D3FE0] transition-colors"
          >
            <RefreshCw className={cn('w-3.5 h-3.5', isLoading && 'animate-spin')} />
            <span>Refresh</span>
          </button>
        </div>
      </div>

      {/* Loading Skeletons */}
      {isLoading && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6" aria-busy="true">
          {[1, 2, 3, 4, 5, 6].map((n) => (
            <div key={n} className="card-base p-6 space-y-4 animate-pulse">
              <div className="w-1/3 h-5 bg-gray-200 rounded" />
              <div className="space-y-2">
                <div className="w-full h-4 bg-gray-200 rounded" />
                <div className="w-2/3 h-4 bg-gray-200 rounded" />
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Error State */}
      {isError && (
        <div className="card-base p-6 bg-red-50 border-red-200 text-red-900 flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <AlertCircle className="w-6 h-6 text-red-600 flex-shrink-0" />
            <div>
              <h3 className="font-semibold text-sm">Failed to load LLM settings</h3>
              <p className="text-xs text-red-700 mt-0.5">
                {error instanceof Error ? error.message : 'Please check connection.'}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => refetch()}
            className="px-3.5 py-1.5 bg-red-600 text-white rounded-lg text-xs font-semibold hover:bg-red-700 flex items-center space-x-1"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            <span>Retry</span>
          </button>
        </div>
      )}

      {/* Settings Grid */}
      {!isLoading && !isError && settings && (
        <div className="space-y-6">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {/* 1. Model Configuration */}
            <div className="card-base p-6 space-y-4">
              <div className="flex items-center space-x-2.5 pb-3 border-b border-[#E5E7EB]">
                <Cpu className="w-5 h-5 text-[#6D3FE0]" />
                <h3 className="text-base font-bold text-[#111827]">Model Configuration</h3>
              </div>
              <dl className="grid grid-cols-2 gap-y-3 gap-x-2 text-xs">
                <div>
                  <dt className="text-gray-500">Active Model</dt>
                  <dd className="font-semibold text-gray-900 font-mono mt-0.5">{settings.model}</dd>
                </div>
                <div>
                  <dt className="text-gray-500">API Connection Mode</dt>
                  <dd className="font-semibold text-gray-900 mt-0.5">{settings.api_mode}</dd>
                </div>
                <div>
                  <dt className="text-gray-500">Structured Output</dt>
                  <dd className="font-semibold text-gray-900 mt-0.5">
                    {settings.structured_output_mode}
                  </dd>
                </div>
                <div>
                  <dt className="text-gray-500">Temperature</dt>
                  <dd className="font-semibold text-gray-900 mt-0.5">{settings.temperature}</dd>
                </div>
                <div>
                  <dt className="text-gray-500">Max Turns</dt>
                  <dd className="font-semibold text-gray-900 mt-0.5">{settings.max_turns}</dd>
                </div>
                <div>
                  <dt className="text-gray-500">Request Timeout</dt>
                  <dd className="font-semibold text-gray-900 mt-0.5">{settings.timeout_s}s</dd>
                </div>
                <div>
                  <dt className="text-gray-500">Max Upstream Retries</dt>
                  <dd className="font-semibold text-gray-900 mt-0.5">{settings.max_retries}</dd>
                </div>
              </dl>
            </div>

            {/* 2. Provider Connectivity */}
            <div className="card-base p-6 space-y-4">
              <div className="flex items-center space-x-2.5 pb-3 border-b border-[#E5E7EB]">
                <Server className="w-5 h-5 text-[#6D3FE0]" />
                <h3 className="text-base font-bold text-[#111827]">Provider Connectivity</h3>
              </div>
              <dl className="grid grid-cols-2 gap-y-3 gap-x-2 text-xs">
                <div>
                  <dt className="text-gray-500">Provider Protocol</dt>
                  <dd className="font-semibold text-gray-900 mt-0.5">{settings.provider}</dd>
                </div>
                <div>
                  <dt className="text-gray-500">Endpoint Base URL</dt>
                  <dd className="font-mono text-gray-900 truncate mt-0.5" title={settings.base_url}>
                    {settings.base_url}
                  </dd>
                </div>
                <div className="col-span-2">
                  <dt className="text-gray-500">API Key Status</dt>
                  <dd className="mt-1">
                    {settings.api_key_configured ? (
                      <span className="inline-flex items-center space-x-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-green-50 text-green-700 border border-green-200">
                        <CheckCircle2 className="w-3.5 h-3.5 text-green-600" />
                        <span>Configured & Valid</span>
                      </span>
                    ) : (
                      <span className="inline-flex items-center space-x-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-amber-50 text-amber-800 border border-amber-200">
                        <AlertTriangle className="w-3.5 h-3.5 text-amber-600" />
                        <span>Not Configured</span>
                      </span>
                    )}
                  </dd>
                </div>
              </dl>
            </div>

            {/* 3. Subsystem Health & Circuit State */}
            <div className="card-base p-6 space-y-4">
              <div className="flex items-center space-x-2.5 pb-3 border-b border-[#E5E7EB]">
                <Shield className="w-5 h-5 text-[#6D3FE0]" />
                <h3 className="text-base font-bold text-[#111827]">Subsystem Health</h3>
              </div>
              <dl className="grid grid-cols-2 gap-y-3 gap-x-2 text-xs">
                <div>
                  <dt className="text-gray-500">Circuit Breaker</dt>
                  <dd className="mt-1">
                    <span
                      className={cn(
                        'inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold uppercase',
                        settings.circuit_state === 'CLOSED'
                          ? 'bg-green-100 text-green-800'
                          : 'bg-red-100 text-red-800'
                      )}
                    >
                      {settings.circuit_state}
                    </span>
                  </dd>
                </div>
                <div>
                  <dt className="text-gray-500">Learning Write-Back</dt>
                  <dd className="font-semibold text-gray-900 mt-0.5">
                    {settings.learning_enabled ? 'Enabled' : 'Disabled'}
                  </dd>
                </div>
                <div>
                  <dt className="text-gray-500">Tracing / Telemetry</dt>
                  <dd className="font-semibold text-gray-900 mt-0.5">
                    {settings.tracing_enabled ? 'Active' : 'Disabled'}
                  </dd>
                </div>
                <div>
                  <dt className="text-gray-500">Last Error Detected</dt>
                  <dd className="font-mono text-gray-700 mt-0.5">
                    {settings.last_error_code || 'None'}
                  </dd>
                </div>
              </dl>
            </div>

            {/* 4. Active Prompt Template Versions */}
            <div className="card-base p-6 space-y-4">
              <div className="flex items-center space-x-2.5 pb-3 border-b border-[#E5E7EB]">
                <FileCode className="w-5 h-5 text-[#6D3FE0]" />
                <h3 className="text-base font-bold text-[#111827]">Prompt Versions</h3>
              </div>
              <dl className="space-y-2.5 text-xs">
                {Object.entries(settings.prompt_versions).map(([k, v]) => (
                  <div key={k} className="flex items-center justify-between">
                    <dt className="text-gray-500 capitalize">{k.replace('_', ' ')}</dt>
                    <dd className="font-mono font-medium text-gray-900 px-2 py-0.5 bg-gray-100 rounded">
                      {v}
                    </dd>
                  </div>
                ))}
              </dl>
            </div>

            {/* 5. Knowledge Store & Vector Indexes */}
            <div className="card-base p-6 space-y-4">
              <div className="flex items-center space-x-2.5 pb-3 border-b border-[#E5E7EB]">
                <Database className="w-5 h-5 text-[#6D3FE0]" />
                <h3 className="text-base font-bold text-[#111827]">Knowledge & Retrieval</h3>
              </div>
              <dl className="grid grid-cols-2 gap-y-3 gap-x-2 text-xs">
                <div className="col-span-2">
                  <dt className="text-gray-500">Sentence Embedding Model</dt>
                  <dd className="font-mono font-semibold text-gray-900 mt-0.5">
                    {settings.embedding_model}
                  </dd>
                </div>
                <div>
                  <dt className="text-gray-500">SAP FAISS Index</dt>
                  <dd className="font-semibold text-gray-900 mt-0.5">
                    {settings.knowledge_index_status.sap_index_built ? 'Built & Active' : 'Missing'}
                  </dd>
                </div>
                <div>
                  <dt className="text-gray-500">SQL FAISS Index</dt>
                  <dd className="font-semibold text-gray-900 mt-0.5">
                    {settings.knowledge_index_status.sql_index_built ? 'Built & Active' : 'Missing'}
                  </dd>
                </div>
                <div>
                  <dt className="text-gray-500">Verified Entries Whitelist</dt>
                  <dd className="font-semibold text-gray-900 mt-0.5">
                    {settings.knowledge_index_status.verified_entries_present
                      ? 'Active'
                      : 'Default'}
                  </dd>
                </div>
              </dl>
            </div>

            {/* 6. Multi-Tier Cache Parameters */}
            <div className="card-base p-6 space-y-4">
              <div className="flex items-center space-x-2.5 pb-3 border-b border-[#E5E7EB]">
                <Layers className="w-5 h-5 text-[#6D3FE0]" />
                <h3 className="text-base font-bold text-[#111827]">Caching Subsystem</h3>
              </div>
              <dl className="grid grid-cols-2 gap-y-3 gap-x-2 text-xs">
                <div>
                  <dt className="text-gray-500">Backend Store</dt>
                  <dd className="font-semibold text-gray-900 uppercase mt-0.5">
                    {String(settings.cache_backend.type || 'memory')}
                  </dd>
                </div>
                <div>
                  <dt className="text-gray-500">L1 BI Cache TTL</dt>
                  <dd className="font-semibold text-gray-900 mt-0.5">
                    {String(settings.cache_backend.l1_ttl_s)}s
                  </dd>
                </div>
                <div>
                  <dt className="text-gray-500">L2 Agent Cache TTL</dt>
                  <dd className="font-semibold text-gray-900 mt-0.5">
                    {String(settings.cache_backend.l2_ttl_s)}s
                  </dd>
                </div>
                <div>
                  <dt className="text-gray-500">L3 Similarity Threshold</dt>
                  <dd className="font-semibold text-gray-900 mt-0.5">
                    {String(settings.cache_backend.similarity_threshold)}
                  </dd>
                </div>
              </dl>
            </div>

            {/* 7. Guardrail Modes & Limits */}
            <div className="card-base p-6 space-y-4">
              <div className="flex items-center space-x-2.5 pb-3 border-b border-[#E5E7EB]">
                <Shield className="w-5 h-5 text-[#6D3FE0]" />
                <h3 className="text-base font-bold text-[#111827]">Security Guardrails</h3>
              </div>
              <dl className="grid grid-cols-2 gap-y-3 gap-x-2 text-xs">
                <div>
                  <dt className="text-gray-500">Max SQL Characters</dt>
                  <dd className="font-semibold text-gray-900 mt-0.5">
                    {Number(settings.guardrail_modes.max_sql_length).toLocaleString()}
                  </dd>
                </div>
                <div>
                  <dt className="text-gray-500">Concurrency Limit</dt>
                  <dd className="font-semibold text-gray-900 mt-0.5">
                    {String(settings.guardrail_modes.concurrency_limit)} parallel runs
                  </dd>
                </div>
                <div>
                  <dt className="text-gray-500">Subset Containment Rule</dt>
                  <dd className="font-semibold text-gray-900 mt-0.5">Enforced (AST)</dd>
                </div>
                <div>
                  <dt className="text-gray-500">Risk Floor Elevation</dt>
                  <dd className="font-semibold text-gray-900 mt-0.5">Enforced</dd>
                </div>
              </dl>
            </div>

            {/* 8. Token Budget & Cost Allocation */}
            <div className="card-base p-6 space-y-4">
              <div className="flex items-center space-x-2.5 pb-3 border-b border-[#E5E7EB]">
                <DollarSign className="w-5 h-5 text-[#6D3FE0]" />
                <h3 className="text-base font-bold text-[#111827]">Budget & Cost Accounting</h3>
              </div>
              <dl className="grid grid-cols-2 gap-y-3 gap-x-2 text-xs">
                <div>
                  <dt className="text-gray-500">Pricing Table</dt>
                  <dd className="font-semibold text-gray-900 mt-0.5">
                    {settings.pricing_configured ? 'Configured' : 'Pricing not configured'}
                  </dd>
                </div>
                <div>
                  <dt className="text-gray-500">Request Token Cap</dt>
                  <dd className="font-semibold text-gray-900 mt-0.5">
                    {Number(settings.token_budgets.max_request_tokens).toLocaleString()} tokens
                  </dd>
                </div>
                <div>
                  <dt className="text-gray-500">Daily Token Allocation</dt>
                  <dd className="font-semibold text-gray-900 mt-0.5">
                    {Number(settings.token_budgets.daily_token_budget).toLocaleString()} tokens
                  </dd>
                </div>
                <div>
                  <dt className="text-gray-500">Tokens Consumed Today</dt>
                  <dd className="font-semibold text-gray-900 mt-0.5">
                    {Number(settings.token_budgets.daily_tokens_used).toLocaleString()} tokens
                  </dd>
                </div>
              </dl>
            </div>
          </div>

          {/* How to change these settings panel */}
          <div className="card-base p-6 bg-slate-50 border-slate-200 space-y-3">
            <div className="flex items-center space-x-2 text-sm font-bold text-slate-900">
              <Info className="w-4 h-4 text-[#6D3FE0]" />
              <h4>How to Change These Settings</h4>
            </div>
            <p className="text-xs text-slate-700 leading-relaxed">
              These settings are governed by environment variables and the root{' '}
              <code className="px-1.5 py-0.5 bg-white border rounded font-mono text-[11px]">
                .env
              </code>{' '}
              file. To modify any parameter, update the corresponding variable in your server
              configuration and restart the application backend.
            </p>
            <div className="pt-2">
              <span className="text-[11px] font-semibold text-slate-800 uppercase tracking-wider block mb-2">
                Governing Environment Variables:
              </span>
              <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-2 font-mono text-[11px] text-slate-700">
                <span className="p-1.5 bg-white border rounded">LLM_MODEL</span>
                <span className="p-1.5 bg-white border rounded">OPENAI_BASE_URL</span>
                <span className="p-1.5 bg-white border rounded">LLM_API_MODE</span>
                <span className="p-1.5 bg-white border rounded">STRUCTURED_OUTPUT_MODE</span>
                <span className="p-1.5 bg-white border rounded">LLM_TEMPERATURE</span>
                <span className="p-1.5 bg-white border rounded">LLM_TIMEOUT_S</span>
                <span className="p-1.5 bg-white border rounded">LLM_MAX_RETRIES</span>
                <span className="p-1.5 bg-white border rounded">CACHE_BACKEND</span>
                <span className="p-1.5 bg-white border rounded">DAILY_TOKEN_BUDGET</span>
                <span className="p-1.5 bg-white border rounded">ENABLE_LEARNING</span>
                <span className="p-1.5 bg-white border rounded">ENABLE_TRACING</span>
                <span className="p-1.5 bg-white border rounded">ADMIN_API_KEY</span>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default LlmSettingsPage;
