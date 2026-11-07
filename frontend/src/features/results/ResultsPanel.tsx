import React, { useState, Suspense } from 'react';
import * as Tabs from '@radix-ui/react-tabs';
import * as Tooltip from '@radix-ui/react-tooltip';
import {
  Copy,
  Check,
  Download,
  ShieldAlert,
  AlertTriangle,
  Info,
  CheckCircle,
  Sparkles,
} from 'lucide-react';
import { toast } from 'sonner';
import type { BusinessIntelligence } from '@/api/schemas';
import { cn, formatMs } from '@/lib/utils';

const SqlEditor = React.lazy(() => import('@/components/SqlEditor'));

export interface ResultsPanelProps {
  result: BusinessIntelligence;
}

export const ResultsPanel: React.FC<ResultsPanelProps> = ({ result }) => {
  const [activeTab, setActiveTab] = useState('summary');
  const [copiedSection, setCopiedSection] = useState<string | null>(null);

  const copyToClipboard = async (text: string, label: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedSection(label);
      toast.success(`${label} copied to clipboard`);
      setTimeout(() => setCopiedSection(null), 2000);
    } catch {
      toast.error('Failed to copy to clipboard');
    }
  };

  const downloadJson = () => {
    try {
      const dataStr =
        'data:text/json;charset=utf-8,' + encodeURIComponent(JSON.stringify(result, null, 2));
      const downloadAnchor = document.createElement('a');
      downloadAnchor.setAttribute('href', dataStr);
      downloadAnchor.setAttribute('download', `analysis-${result.meta.request_id}.json`);
      document.body.appendChild(downloadAnchor);
      downloadAnchor.click();
      downloadAnchor.remove();
      toast.success('JSON file downloaded');
    } catch {
      toast.error('Failed to download JSON');
    }
  };

  // Cache Badge Label
  const cacheStatusMap: Record<string, { label: string; class: string }> = {
    hit_final: { label: 'Cached', class: 'bg-emerald-100 text-emerald-800' },
    partial: { label: 'Partial cache', class: 'bg-blue-100 text-blue-800' },
    miss: { label: 'Fresh', class: 'bg-purple-100 text-purple-800' },
    refresh: { label: 'Refreshed', class: 'bg-indigo-100 text-indigo-800' },
  };
  const cacheInfo = cacheStatusMap[result.meta.cache_status] || {
    label: result.meta.cache_status,
    class: 'bg-gray-100 text-gray-800',
  };

  // Risk Level Badges
  const riskColorMap = {
    low: {
      bg: 'bg-green-100',
      text: 'text-green-800',
      border: 'border-green-200',
      icon: CheckCircle,
    },
    medium: {
      bg: 'bg-amber-100',
      text: 'text-amber-800',
      border: 'border-amber-200',
      icon: AlertTriangle,
    },
    high: {
      bg: 'bg-orange-100',
      text: 'text-orange-800',
      border: 'border-orange-200',
      icon: ShieldAlert,
    },
    critical: {
      bg: 'bg-red-100',
      text: 'text-red-800',
      border: 'border-red-200',
      icon: ShieldAlert,
    },
  };
  const riskInfo = riskColorMap[result.risk_summary.level] || riskColorMap.medium;
  const RiskIcon = riskInfo.icon;

  const gov = result.governance;
  const meta = result.meta;

  return (
    <div className="card-base p-6 space-y-6 mt-8" id="analysis-results">
      {/* 1. Sticky Meta Strip */}
      <div className="sticky top-16 z-30 bg-white/95 backdrop-blur-sm -mx-6 -mt-6 p-4 border-b border-[#E5E7EB] flex flex-wrap items-center justify-between gap-3 shadow-xs">
        <div className="flex flex-wrap items-center gap-2">
          {/* Cache badge */}
          <span className={cn('px-2.5 py-0.5 rounded-full text-xs font-semibold', cacheInfo.class)}>
            {cacheInfo.label}
          </span>

          {/* Query Type */}
          <span className="px-2.5 py-0.5 rounded-md text-xs font-medium bg-[#F3F4F6] text-[#4B5563]">
            {result.query_type}
          </span>

          {/* SAP Module */}
          <span className="px-2.5 py-0.5 rounded-md text-xs font-medium bg-blue-50 text-blue-700 border border-blue-200">
            SAP: {result.sap_module}
          </span>

          {/* Risk Level Badge */}
          <span
            className={cn(
              'inline-flex items-center space-x-1 px-2.5 py-0.5 rounded-full text-xs font-bold border',
              riskInfo.bg,
              riskInfo.text,
              riskInfo.border
            )}
          >
            <RiskIcon className="w-3.5 h-3.5" />
            <span className="capitalize">{result.risk_summary.level} Risk</span>
          </span>

          {/* Degraded Badge with Tooltip */}
          {meta.degraded && (
            <Tooltip.Provider>
              <Tooltip.Root>
                <Tooltip.Trigger asChild>
                  <button
                    type="button"
                    className="inline-flex items-center space-x-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-amber-100 text-amber-800 border border-amber-200 cursor-help"
                  >
                    <AlertTriangle className="w-3 h-3 text-amber-600" />
                    <span>Degraded</span>
                  </button>
                </Tooltip.Trigger>
                <Tooltip.Portal>
                  <Tooltip.Content
                    className="max-w-xs bg-gray-900 text-white text-xs rounded-md p-2 shadow-lg z-50"
                    sideOffset={5}
                  >
                    <p className="font-bold mb-1">Degraded Reasons:</p>
                    <ul className="list-disc pl-4 space-y-0.5">
                      {meta.degraded_reasons.map((r, i) => (
                        <li key={i}>{r}</li>
                      ))}
                    </ul>
                  </Tooltip.Content>
                </Tooltip.Portal>
              </Tooltip.Root>
            </Tooltip.Provider>
          )}

          {/* AI-Generated Label */}
          {meta.ai_generated && (
            <span className="inline-flex items-center space-x-1 px-2 py-0.5 rounded text-[11px] font-medium bg-purple-50 text-purple-700 border border-purple-200">
              <Sparkles className="w-3 h-3" />
              <span>AI-generated</span>
            </span>
          )}
        </div>

        {/* Request ID + Copy */}
        <div className="flex items-center space-x-2 text-xs text-[#6B7280]">
          <span className="font-mono">Req: {meta.request_id}</span>
          <button
            type="button"
            onClick={() => copyToClipboard(meta.request_id, 'Request ID')}
            className="p-1 hover:text-[#111827] focus-visible:ring-1 focus-visible:ring-[#6D3FE0] rounded"
            aria-label="Copy Request ID"
            title="Copy Request ID"
          >
            {copiedSection === 'Request ID' ? (
              <Check className="w-3.5 h-3.5 text-green-600" />
            ) : (
              <Copy className="w-3.5 h-3.5" />
            )}
          </button>
        </div>
      </div>

      {/* Degraded Alert Banner */}
      {meta.degraded && meta.degraded_reasons.length > 0 && (
        <div className="p-4 bg-amber-50 border border-amber-200 rounded-lg text-amber-900 text-sm space-y-1">
          <div className="flex items-center space-x-2 font-bold text-amber-800">
            <AlertTriangle className="w-4 h-4 text-amber-600" />
            <span>Analysis Executed in Degraded Mode</span>
          </div>
          <ul className="list-disc pl-6 text-xs text-amber-700 space-y-0.5">
            {meta.degraded_reasons.map((reason, idx) => (
              <li key={idx} className="whitespace-pre-wrap">
                {reason}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Warnings List */}
      {meta.warnings && meta.warnings.length > 0 && (
        <div className="p-4 bg-yellow-50 border border-yellow-200 rounded-lg text-yellow-900 text-sm space-y-1">
          <div className="flex items-center space-x-2 font-bold text-yellow-800">
            <Info className="w-4 h-4 text-yellow-600" />
            <span>Audit Warnings</span>
          </div>
          <ul className="list-disc pl-6 text-xs text-yellow-800 space-y-0.5">
            {meta.warnings.map((w, idx) => (
              <li key={idx} className="whitespace-pre-wrap">
                {w}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Tabs Navigation */}
      <Tabs.Root value={activeTab} onValueChange={setActiveTab}>
        <Tabs.List
          className="flex border-b border-[#E5E7EB] space-x-4 overflow-x-auto"
          aria-label="Analysis Results Sections"
        >
          {[
            { id: 'summary', label: 'Summary' },
            { id: 'governance', label: 'Governance' },
            { id: 'queries', label: 'Queries' },
            { id: 'run-details', label: 'Run Details' },
            { id: 'raw-json', label: 'Raw JSON' },
          ].map((tab) => (
            <Tabs.Trigger
              key={tab.id}
              value={tab.id}
              className={cn(
                'py-2 px-3 text-sm font-medium border-b-2 transition-colors whitespace-nowrap focus-visible:ring-2 focus-visible:ring-[#6D3FE0] rounded-t-md',
                activeTab === tab.id
                  ? 'border-[#6D3FE0] text-[#6D3FE0] font-semibold'
                  : 'border-transparent text-[#6B7280] hover:text-[#111827]'
              )}
            >
              {tab.label}
            </Tabs.Trigger>
          ))}
        </Tabs.List>

        {/* TAB 1: SUMMARY */}
        <Tabs.Content value="summary" className="pt-6 space-y-6 focus:outline-none">
          {/* Executive Business Meaning */}
          <div className="space-y-2">
            <h3 className="text-sm font-bold text-[#111827] uppercase tracking-wider text-xs">
              Executive Business Narrative
            </h3>
            <p className="text-sm text-[#374151] whitespace-pre-wrap leading-relaxed bg-[#F8F9FB] p-4 rounded-lg border border-[#E5E7EB]">
              {result.business_meaning}
            </p>
          </div>

          {/* Technical Summary */}
          <div className="space-y-2">
            <h3 className="text-sm font-bold text-[#111827] uppercase tracking-wider text-xs">
              Technical Summary
            </h3>
            <p className="text-sm text-[#374151] whitespace-pre-wrap leading-relaxed bg-[#F8F9FB] p-4 rounded-lg border border-[#E5E7EB]">
              {result.technical_summary}
            </p>
          </div>

          {/* Affected Entities */}
          <div className="space-y-2">
            <h3 className="text-sm font-bold text-[#111827] uppercase tracking-wider text-xs">
              Affected SAP Tables & Entities
            </h3>
            <div className="flex flex-wrap gap-2">
              {result.affected_entities.map((entity, i) => (
                <span
                  key={i}
                  className="px-3 py-1 bg-white border border-[#E5E7EB] rounded-md text-xs font-mono font-semibold text-[#111827] shadow-xs"
                >
                  {entity}
                </span>
              ))}
            </div>
          </div>

          {/* Risk Summary */}
          <div className="p-4 bg-[#F8F9FB] rounded-lg border border-[#E5E7EB] space-y-4">
            <div className="flex items-center space-x-2">
              <RiskIcon className="w-5 h-5 text-amber-600" />
              <h3 className="text-sm font-bold text-[#111827]">
                Risk Assessment ({result.risk_summary.level.toUpperCase()})
              </h3>
            </div>

            {/* Identified Hazards */}
            <div className="space-y-1">
              <span className="text-xs font-semibold text-[#4B5563]">Identified Hazards:</span>
              <ul className="list-disc pl-5 text-xs text-[#374151] space-y-1">
                {result.risk_summary.items.map((item, idx) => (
                  <li key={idx} className="whitespace-pre-wrap">
                    {item}
                  </li>
                ))}
              </ul>
            </div>

            {/* Recommendations */}
            <div className="space-y-1 pt-2 border-t border-[#E5E7EB]">
              <span className="text-xs font-semibold text-[#4B5563]">
                Architectural Recommendations:
              </span>
              <ul className="list-disc pl-5 text-xs text-[#374151] space-y-1">
                {result.risk_summary.recommendations.map((rec, idx) => (
                  <li key={idx} className="whitespace-pre-wrap">
                    {rec}
                  </li>
                ))}
              </ul>
            </div>
          </div>

          {/* Final Learning */}
          {result.final_learning && (
            <div className="p-4 bg-purple-50 border border-purple-200 rounded-lg text-purple-900 space-y-1">
              <div className="text-xs font-bold uppercase tracking-wider text-purple-700">
                Grounded Knowledge Learning
              </div>
              <p className="text-xs text-purple-900 whitespace-pre-wrap">{result.final_learning}</p>
            </div>
          )}
        </Tabs.Content>

        {/* TAB 2: GOVERNANCE */}
        <Tabs.Content value="governance" className="pt-6 space-y-4 focus:outline-none">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Subject Area */}
            <div className="card-base p-4 space-y-1">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-[#6B7280]">Subject Area</span>
                {gov.is_new_flags.subject_area && (
                  <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-purple-100 text-purple-800">
                    New
                  </span>
                )}
              </div>
              <p className="text-sm font-bold text-[#111827] whitespace-pre-wrap">
                {gov.subject_area}
              </p>
            </div>

            {/* Business Process */}
            <div className="card-base p-4 space-y-1">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-[#6B7280]">Business Process</span>
                {gov.is_new_flags.business_process && (
                  <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-purple-100 text-purple-800">
                    New
                  </span>
                )}
              </div>
              <p className="text-sm font-bold text-[#111827] whitespace-pre-wrap">
                {gov.business_process}
              </p>
            </div>

            {/* Dataset */}
            <div className="card-base p-4 space-y-1 md:col-span-2">
              <span className="text-xs font-semibold text-[#6B7280]">Dataset</span>
              <p className="text-sm font-bold text-[#111827] whitespace-pre-wrap">{gov.dataset}</p>
            </div>

            {/* Business Concept */}
            <div className="card-base p-4 space-y-2 md:col-span-2">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-[#6B7280]">Business Concept</span>
                {gov.is_new_flags.business_concept && (
                  <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-purple-100 text-purple-800">
                    New
                  </span>
                )}
              </div>
              <div className="text-sm font-bold text-[#111827] whitespace-pre-wrap">
                {gov.business_concept.term}{' '}
                {gov.business_concept.field && (
                  <span className="font-mono text-xs font-normal text-[#6B7280]">
                    ({gov.business_concept.field})
                  </span>
                )}
              </div>
              <p className="text-xs text-[#4B5563] whitespace-pre-wrap leading-relaxed">
                {gov.business_concept.description}
              </p>
            </div>

            {/* Rule Name */}
            <div className="card-base p-4 space-y-1 md:col-span-2">
              <span className="text-xs font-semibold text-[#6B7280]">Rule Name</span>
              <p className="text-sm font-bold text-[#111827] whitespace-pre-wrap">{gov.rule}</p>
            </div>

            {/* Specification */}
            <div className="card-base p-4 space-y-1 md:col-span-2">
              <span className="text-xs font-semibold text-[#6B7280]">Functional Specification</span>
              <p className="text-xs text-[#374151] whitespace-pre-wrap leading-relaxed">
                {gov.specification}
              </p>
            </div>

            {/* Enforcement */}
            <div className="card-base p-4 space-y-1">
              <span className="text-xs font-semibold text-[#6B7280]">Enforcement Action</span>
              <p className="text-xs text-[#374151] whitespace-pre-wrap">{gov.enforcement}</p>
            </div>

            {/* Implementation */}
            <div className="card-base p-4 space-y-1">
              <span className="text-xs font-semibold text-[#6B7280]">Technical Implementation</span>
              <p className="text-xs font-mono text-[#374151] whitespace-pre-wrap">
                {gov.implementation}
              </p>
            </div>

            {/* Opportunity Definition */}
            <div className="card-base p-4 space-y-1">
              <span className="text-xs font-semibold text-[#6B7280]">Opportunity Definition</span>
              <p className="text-xs text-[#374151] whitespace-pre-wrap leading-relaxed">
                {gov.opportunity_definition}
              </p>
            </div>

            {/* Error Definition */}
            <div className="card-base p-4 space-y-1">
              <span className="text-xs font-semibold text-[#6B7280]">Error Definition</span>
              <p className="text-xs text-[#374151] whitespace-pre-wrap leading-relaxed">
                {gov.error_definition}
              </p>
            </div>
          </div>
        </Tabs.Content>

        {/* TAB 3: QUERIES */}
        <Tabs.Content value="queries" className="pt-6 space-y-6 focus:outline-none">
          <div className="p-3 bg-purple-50 border border-purple-200 rounded-lg text-purple-900 text-xs font-medium">
            AI-generated SQL. Review before running. This tool never executes it.
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Opportunity Query Box */}
            <div className="card-base p-4 space-y-3 flex flex-col justify-between">
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <h4 className="text-sm font-bold text-[#111827]">Opportunity Query</h4>
                  <div className="flex items-center space-x-2">
                    <span
                      className={cn(
                        'px-2 py-0.5 rounded text-[10px] font-bold',
                        meta.validation.opportunity_sql_ok
                          ? 'bg-green-100 text-green-800'
                          : 'bg-red-100 text-red-800'
                      )}
                    >
                      {meta.validation.opportunity_sql_ok ? 'Valid SQL' : 'Invalid SQL'}
                    </span>
                    <button
                      type="button"
                      onClick={() => copyToClipboard(gov.opportunity_query, 'Opportunity Query')}
                      className="p-1 text-[#6B7280] hover:text-[#111827] rounded focus-visible:ring-1 focus-visible:ring-[#6D3FE0]"
                      aria-label="Copy Opportunity Query"
                    >
                      {copiedSection === 'Opportunity Query' ? (
                        <Check className="w-4 h-4 text-green-600" />
                      ) : (
                        <Copy className="w-4 h-4" />
                      )}
                    </button>
                  </div>
                </div>

                <p className="text-xs text-[#6B7280] whitespace-pre-wrap">
                  {gov.opportunity_definition}
                </p>
              </div>

              {gov.opportunity_query ? (
                <Suspense fallback={<div className="h-[180px] bg-gray-50 animate-pulse rounded" />}>
                  <SqlEditor
                    value={gov.opportunity_query}
                    readOnly={true}
                    minHeight="180px"
                    aria-label="Opportunity SQL Query"
                  />
                </Suspense>
              ) : (
                <div className="p-4 bg-gray-50 border border-dashed border-gray-300 rounded text-center text-xs text-gray-500">
                  Not generated, see warnings
                </div>
              )}
            </div>

            {/* Error Query Box */}
            <div className="card-base p-4 space-y-3 flex flex-col justify-between">
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <h4 className="text-sm font-bold text-[#111827]">Error Query</h4>
                  <div className="flex items-center space-x-2">
                    <span
                      className={cn(
                        'px-2 py-0.5 rounded text-[10px] font-bold',
                        meta.validation.error_sql_ok
                          ? 'bg-green-100 text-green-800'
                          : 'bg-red-100 text-red-800'
                      )}
                    >
                      {meta.validation.error_sql_ok ? 'Valid SQL' : 'Invalid SQL'}
                    </span>

                    <span
                      className={cn(
                        'px-2 py-0.5 rounded text-[10px] font-bold',
                        meta.validation.subset_ok
                          ? 'bg-emerald-100 text-emerald-800'
                          : 'bg-red-100 text-red-800'
                      )}
                    >
                      {meta.validation.subset_ok ? 'Subset check passed' : 'Subset check failed'}
                    </span>

                    <button
                      type="button"
                      onClick={() => copyToClipboard(gov.error_query, 'Error Query')}
                      className="p-1 text-[#6B7280] hover:text-[#111827] rounded focus-visible:ring-1 focus-visible:ring-[#6D3FE0]"
                      aria-label="Copy Error Query"
                    >
                      {copiedSection === 'Error Query' ? (
                        <Check className="w-4 h-4 text-green-600" />
                      ) : (
                        <Copy className="w-4 h-4" />
                      )}
                    </button>
                  </div>
                </div>

                <p className="text-xs text-[#6B7280] whitespace-pre-wrap">{gov.error_definition}</p>
              </div>

              {gov.error_query ? (
                <Suspense fallback={<div className="h-[180px] bg-gray-50 animate-pulse rounded" />}>
                  <SqlEditor
                    value={gov.error_query}
                    readOnly={true}
                    minHeight="180px"
                    aria-label="Error SQL Query"
                  />
                </Suspense>
              ) : (
                <div className="p-4 bg-gray-50 border border-dashed border-gray-300 rounded text-center text-xs text-gray-500">
                  Not generated, see warnings
                </div>
              )}
            </div>
          </div>

          {/* Baseline Validation Chip */}
          <div className="pt-2">
            <span
              className={cn(
                'inline-flex items-center space-x-1.5 px-3 py-1 rounded-md text-xs font-semibold',
                meta.validation.matches_parser_baseline
                  ? 'bg-blue-50 text-blue-700 border border-blue-200'
                  : 'bg-yellow-50 text-yellow-800 border border-yellow-200'
              )}
            >
              <Info className="w-3.5 h-3.5" />
              <span>
                {meta.validation.matches_parser_baseline
                  ? 'Matches parser baseline benchmark'
                  : 'Differs from parser baseline reference'}
              </span>
            </span>
          </div>
        </Tabs.Content>

        {/* TAB 4: RUN DETAILS */}
        <Tabs.Content value="run-details" className="pt-6 space-y-6 focus:outline-none">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 text-xs">
            {/* Model & Versions */}
            <div className="card-base p-4 space-y-2">
              <span className="font-bold text-[#111827] block">Model & Prompts</span>
              <div>
                Model: <span className="font-mono font-semibold">{meta.model}</span>
              </div>
              <div>
                SQL Agent Prompt: <span className="font-mono">{meta.prompt_versions.sql}</span>
              </div>
              <div>
                SAP Agent Prompt: <span className="font-mono">{meta.prompt_versions.sap}</span>
              </div>
              <div>
                Synthesizer Prompt:{' '}
                <span className="font-mono">{meta.prompt_versions.synthesizer}</span>
              </div>
            </div>

            {/* Cache Resolution per Agent */}
            <div className="card-base p-4 space-y-2">
              <span className="font-bold text-[#111827] block">Cache Status</span>
              <div>
                Overall: <span className="font-semibold">{meta.cache_status}</span>
              </div>
              <div>
                SQL Agent: <span className="font-semibold">{meta.cache_detail.sql_agent}</span>
              </div>
              <div>
                SAP Agent: <span className="font-semibold">{meta.cache_detail.sap_agent}</span>
              </div>
              {meta.similarity_score != null && (
                <div>
                  Similarity Score: <span className="font-semibold">{meta.similarity_score}</span>
                </div>
              )}
            </div>

            {/* Token Usage & Cost */}
            <div className="card-base p-4 space-y-2">
              <span className="font-bold text-[#111827] block">Tokens & Cost</span>
              <div>SQL Agent: {meta.token_usage.sql.toLocaleString()}</div>
              <div>SAP Agent: {meta.token_usage.sap.toLocaleString()}</div>
              <div>Synthesizer: {meta.token_usage.synthesizer.toLocaleString()}</div>
              <div>Embedding: {(meta.token_usage.embedding ?? 0).toLocaleString()}</div>
              <div className="font-bold pt-1 border-t border-[#E5E7EB]">
                Total Tokens: {meta.token_usage.total.toLocaleString()}
              </div>
              {meta.estimated_cost_usd != null && (
                <div className="text-emerald-700 font-semibold">
                  Estimated Cost: ${meta.estimated_cost_usd.toFixed(4)}
                </div>
              )}
            </div>

            {/* Stage & Agent Timings */}
            <div className="card-base p-4 space-y-2 md:col-span-2">
              <span className="font-bold text-[#111827] block">Timings (ms)</span>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <span className="font-semibold text-gray-700">Stages:</span>
                  <ul className="mt-1 space-y-0.5">
                    {Object.entries(meta.stage_timings_ms).map(([st, ms]) => (
                      <li key={st} className="flex justify-between">
                        <span>{st}:</span>
                        <span className="font-mono">{formatMs(ms)}</span>
                      </li>
                    ))}
                  </ul>
                </div>
                <div>
                  <span className="font-semibold text-gray-700">Agents:</span>
                  <ul className="mt-1 space-y-0.5">
                    {Object.entries(meta.agent_timings_ms).map(([ag, ms]) => (
                      <li key={ag} className="flex justify-between">
                        <span>{ag}:</span>
                        <span className="font-mono">{formatMs(ms)}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </div>

            {/* Parse Quality & Naming Violations */}
            <div className="card-base p-4 space-y-2">
              <span className="font-bold text-[#111827] block">Quality & Standards</span>
              <div>
                Parse Quality: <span className="font-semibold">{meta.parse_quality}</span>
              </div>
              <div>
                Naming Violations:
                {meta.naming_violations.length === 0 ? (
                  <span className="text-green-700 ml-1 font-semibold">None</span>
                ) : (
                  <ul className="list-disc pl-4 mt-1 text-red-700 space-y-0.5">
                    {meta.naming_violations.map((v, i) => (
                      <li key={i}>{v}</li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          </div>

          {/* Guardrails Report */}
          <div className="card-base p-4 space-y-3 text-xs">
            <div className="flex items-center justify-between">
              <span className="font-bold text-sm text-[#111827] flex items-center space-x-1.5">
                <ShieldAlert className="w-4 h-4 text-[#6D3FE0]" />
                <span>Guardrails Event Report</span>
              </span>
              <span className="text-[#6B7280]">{meta.guardrails.events.length} events logged</span>
            </div>

            {meta.guardrails.events.length === 0 ? (
              <p className="text-gray-500">No security or safety events triggered.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse">
                  <thead>
                    <tr className="border-b border-[#E5E7EB] text-[#6B7280]">
                      <th className="py-2 px-3 font-semibold">Severity</th>
                      <th className="py-2 px-3 font-semibold">Check Name</th>
                      <th className="py-2 px-3 font-semibold">Stage</th>
                      <th className="py-2 px-3 font-semibold">Action</th>
                      <th className="py-2 px-3 font-semibold">Message</th>
                    </tr>
                  </thead>
                  <tbody>
                    {meta.guardrails.events.map((ev) => (
                      <tr key={ev.id} className="border-b border-gray-100 hover:bg-gray-50">
                        <td className="py-2 px-3">
                          <span
                            className={cn(
                              'px-2 py-0.5 rounded text-[10px] font-bold uppercase',
                              ev.severity === 'critical'
                                ? 'bg-red-100 text-red-800'
                                : ev.severity === 'high'
                                  ? 'bg-orange-100 text-orange-800'
                                  : ev.severity === 'medium'
                                    ? 'bg-amber-100 text-amber-800'
                                    : 'bg-gray-100 text-gray-800'
                            )}
                          >
                            {ev.severity}
                          </span>
                        </td>
                        <td className="py-2 px-3 font-mono font-medium">{ev.name}</td>
                        <td className="py-2 px-3 text-gray-600">{ev.stage}</td>
                        <td className="py-2 px-3 font-semibold">{ev.action}</td>
                        <td className="py-2 px-3 text-gray-700 whitespace-pre-wrap max-w-md">
                          {ev.message}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </Tabs.Content>

        {/* TAB 5: RAW JSON */}
        <Tabs.Content value="raw-json" className="pt-6 space-y-4 focus:outline-none">
          <div className="flex items-center justify-between">
            <span className="text-xs text-[#6B7280]">Complete BusinessIntelligence Object</span>
            <div className="flex items-center space-x-2">
              <button
                type="button"
                onClick={() => copyToClipboard(JSON.stringify(result, null, 2), 'Raw JSON')}
                className="inline-flex items-center space-x-1 px-3 py-1.5 border border-[#E5E7EB] rounded-md text-xs font-semibold text-[#374151] hover:bg-gray-50 focus-visible:ring-2 focus-visible:ring-[#6D3FE0]"
              >
                {copiedSection === 'Raw JSON' ? (
                  <Check className="w-3.5 h-3.5 text-green-600" />
                ) : (
                  <Copy className="w-3.5 h-3.5" />
                )}
                <span>Copy JSON</span>
              </button>
              <button
                type="button"
                onClick={downloadJson}
                className="inline-flex items-center space-x-1 px-3 py-1.5 border border-[#E5E7EB] rounded-md text-xs font-semibold text-[#374151] hover:bg-gray-50 focus-visible:ring-2 focus-visible:ring-[#6D3FE0]"
              >
                <Download className="w-3.5 h-3.5" />
                <span>Download</span>
              </button>
            </div>
          </div>

          <pre className="p-4 bg-[#111827] text-gray-100 rounded-lg text-xs font-mono overflow-x-auto max-h-[600px] select-all">
            {JSON.stringify(result, null, 2)}
          </pre>
        </Tabs.Content>
      </Tabs.Root>
    </div>
  );
};
