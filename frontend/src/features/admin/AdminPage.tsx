import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Shield,
  Key,
  Cpu,
  Database,
  Trash2,
  Check,
  X,
  AlertTriangle,
  RefreshCw,
  Zap,
} from 'lucide-react';
import * as Dialog from '@radix-ui/react-dialog';
import * as Tabs from '@radix-ui/react-tabs';
import { toast } from 'sonner';
import { apiFetch, ApiError } from '@/api/client';
import {
  CacheStatsSchema,
  LLMStatusResponseSchema,
  AdminPingResponseSchema,
  LearnedSapListSchema,
  GlossaryProposalsListSchema,
  type CacheStats,
  type LLMStatusResponse,
  type AdminPingResponse,
  type LearnedSapEntry,
} from '@/api/schemas';
import { cn } from '@/lib/utils';

export const AdminPage: React.FC = () => {
  // Admin key stored strictly in memory; never persisted
  const [adminKey, setAdminKey] = useState<string>('');
  const [submittedKey, setSubmittedKey] = useState<string | null>(null);

  const [clearDialogOpen, setClearDialogOpen] = useState(false);
  const [clearKind, setClearKind] = useState<'all' | 'l1' | 'l2' | 'l3'>('all');

  const [confirmAction, setConfirmAction] = useState<{
    type: 'approve' | 'reject';
    entry: LearnedSapEntry;
  } | null>(null);

  const [statusFilter, setStatusFilter] = useState<'all' | 'pending' | 'approved' | 'rejected'>(
    'all'
  );

  const queryClient = useQueryClient();

  // Cache stats query
  const {
    data: cacheStats,
    error: cacheError,
    isLoading: cacheLoading,
    refetch: refetchCache,
  } = useQuery<CacheStats>({
    queryKey: ['admin-cache-stats', submittedKey],
    queryFn: () =>
      apiFetch<CacheStats>('/api/v1/cache/stats', {
        adminKey: submittedKey,
        schema: CacheStatsSchema,
      }),
    enabled: Boolean(submittedKey),
    retry: false,
  });

  // LLM Status query
  const { data: llmStatus } = useQuery<LLMStatusResponse>({
    queryKey: ['admin-llm-status', submittedKey],
    queryFn: () =>
      apiFetch<LLMStatusResponse>('/api/v1/llm/status', {
        schema: LLMStatusResponseSchema,
      }),
    enabled: Boolean(submittedKey),
    retry: false,
  });

  // Learned knowledge query
  const {
    data: learnedSap,
    isLoading: learnedLoading,
    refetch: refetchLearned,
  } = useQuery<LearnedSapEntry[]>({
    queryKey: ['admin-learned', submittedKey, statusFilter],
    queryFn: () =>
      apiFetch<LearnedSapEntry[]>(`/api/v1/admin/learned?status=${statusFilter}`, {
        adminKey: submittedKey,
        schema: LearnedSapListSchema,
      }),
    enabled: Boolean(submittedKey),
    retry: false,
  });

  // Glossary proposals query
  const { data: proposals, isLoading: proposalsLoading } = useQuery<Array<Record<string, unknown>>>(
    {
      queryKey: ['admin-glossary-proposals', submittedKey],
      queryFn: () =>
        apiFetch<Array<Record<string, unknown>>>('/api/v1/admin/glossary/proposals', {
          adminKey: submittedKey,
          schema: GlossaryProposalsListSchema,
        }),
      enabled: Boolean(submittedKey),
      retry: false,
    }
  );

  // Clear cache mutation
  const clearMutation = useMutation({
    mutationFn: (kind: string) =>
      apiFetch<{ cleared: boolean; kind: string }>(`/api/v1/cache?kind=${kind}`, {
        method: 'DELETE',
        adminKey: submittedKey,
      }),
    onSuccess: (res) => {
      toast.success(`Cache cleared (${res.kind})`);
      setClearDialogOpen(false);
      queryClient.invalidateQueries({ queryKey: ['admin-cache-stats'] });
    },
    onError: (err) => {
      toast.error(`Failed to clear cache: ${err instanceof Error ? err.message : String(err)}`);
    },
  });

  // Ping LLM mutation
  const pingMutation = useMutation({
    mutationFn: () =>
      apiFetch<AdminPingResponse>('/api/v1/admin/llm/ping', {
        method: 'POST',
        adminKey: submittedKey,
        schema: AdminPingResponseSchema,
      }),
    onSuccess: (res) => {
      if (res.status === 'ok') {
        toast.success(`Ping OK: ${res.latency_ms}ms`);
      } else {
        toast.error(`Ping returned ${res.status}: ${res.message}`);
      }
    },
    onError: (err) => {
      toast.error(`Ping failed: ${err instanceof Error ? err.message : String(err)}`);
    },
  });

  // Approve/Reject learned knowledge mutation
  const curateMutation = useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'approve' | 'reject' }) =>
      apiFetch<{ id: string; status: string; auditor: string }>(
        `/api/v1/admin/learned/${id}/${action}`,
        {
          method: 'POST',
          adminKey: submittedKey,
        }
      ),
    onSuccess: (_, variables) => {
      toast.success(`Entry marked as ${variables.action}d`);
      setConfirmAction(null);
      refetchLearned();
    },
    onError: (err) => {
      toast.error(`Failed to update status: ${err instanceof Error ? err.message : String(err)}`);
    },
  });

  const isAuthError =
    cacheError instanceof ApiError && (cacheError.status === 401 || cacheError.status === 403);
  const is404Error = cacheError instanceof ApiError && cacheError.status === 404;

  const handleSubmitKey = (e: React.FormEvent) => {
    e.preventDefault();
    setSubmittedKey(adminKey);
  };

  return (
    <div className="space-y-8 max-w-6xl mx-auto py-4">
      {/* Page Title */}
      <div>
        <h1 className="text-3xl font-extrabold tracking-tight text-[#111827] flex items-center space-x-2">
          <Shield className="w-8 h-8 text-[#6D3FE0]" />
          <span>System Administration</span>
        </h1>
        <p className="mt-1 text-sm text-[#6B7280]">
          Manage multi-tier caches, monitor live LLM connections, and curate learned governance
          knowledge.
        </p>
      </div>

      {/* Admin Key Input Card */}
      {!submittedKey || isAuthError || is404Error ? (
        <div className="card-base p-6 max-w-md space-y-4">
          <div className="flex items-center space-x-2">
            <Key className="w-5 h-5 text-[#6D3FE0]" />
            <h2 className="font-bold text-base text-[#111827]">Enter Admin Credentials</h2>
          </div>
          <p className="text-xs text-[#6B7280]">
            The admin key is kept strictly in volatile memory and is never written to disk or
            storage.
          </p>

          <form onSubmit={handleSubmitKey} className="space-y-4">
            <div>
              <label
                htmlFor="admin-key"
                className="block text-xs font-semibold text-[#374151] mb-1"
              >
                Admin Secret Key (X-Admin-Key)
              </label>
              <input
                id="admin-key"
                type="password"
                value={adminKey}
                onChange={(e) => setAdminKey(e.target.value)}
                placeholder="Enter secret key..."
                className="w-full px-3 py-2 text-sm border border-[#E5E7EB] rounded-lg focus:outline-none focus:ring-2 focus:ring-[#6D3FE0]"
                required
              />
            </div>

            {isAuthError && (
              <div className="p-3 bg-red-50 border border-red-200 rounded text-xs text-red-800 font-medium">
                Invalid admin key. Access denied.
              </div>
            )}

            {is404Error && (
              <div className="p-3 bg-amber-50 border border-amber-200 rounded text-xs text-amber-800 font-medium">
                Admin API is not enabled on the server.
              </div>
            )}

            <button
              type="submit"
              className="w-full py-2 px-4 gradient-brand text-white text-sm font-semibold rounded-lg hover:opacity-90 shadow-sm focus-visible:ring-2 focus-visible:ring-[#6D3FE0]"
            >
              Authenticate Session
            </button>
          </form>
        </div>
      ) : (
        <div className="space-y-6">
          {/* Top Bar with Sign Out / Re-auth */}
          <div className="flex items-center justify-between card-base p-4">
            <div className="flex items-center space-x-2 text-xs">
              <span className="w-2.5 h-2.5 rounded-full bg-green-500" />
              <span className="font-semibold text-gray-900">Admin Session Authenticated</span>
            </div>
            <button
              type="button"
              onClick={() => {
                setSubmittedKey(null);
                setAdminKey('');
              }}
              className="px-3 py-1 text-xs border border-gray-300 rounded hover:bg-gray-50 text-gray-600"
            >
              Clear Session Key
            </button>
          </div>

          {/* Admin Tabs */}
          <Tabs.Root defaultValue="overview">
            <Tabs.List className="flex border-b border-[#E5E7EB] space-x-4 mb-6">
              <Tabs.Trigger
                value="overview"
                className="py-2 px-3 text-sm font-medium border-b-2 border-transparent data-[state=active]:border-[#6D3FE0] data-[state=active]:text-[#6D3FE0] text-[#6B7280]"
              >
                Overview & Caches
              </Tabs.Trigger>
              <Tabs.Trigger
                value="learned"
                className="py-2 px-3 text-sm font-medium border-b-2 border-transparent data-[state=active]:border-[#6D3FE0] data-[state=active]:text-[#6D3FE0] text-[#6B7280]"
              >
                Learned Knowledge
              </Tabs.Trigger>
              <Tabs.Trigger
                value="glossary"
                className="py-2 px-3 text-sm font-medium border-b-2 border-transparent data-[state=active]:border-[#6D3FE0] data-[state=active]:text-[#6D3FE0] text-[#6B7280]"
              >
                Glossary Proposals
              </Tabs.Trigger>
            </Tabs.List>

            {/* TAB 1: OVERVIEW & CACHES */}
            <Tabs.Content value="overview" className="space-y-6">
              {/* LLM Status Banner & Ping Button */}
              <div className="card-base p-6 space-y-4">
                <div className="flex items-center justify-between">
                  <h2 className="text-base font-bold text-[#111827] flex items-center space-x-2">
                    <Cpu className="w-5 h-5 text-[#6D3FE0]" />
                    <span>LLM Subsystem Probe</span>
                  </h2>

                  <button
                    type="button"
                    onClick={() => pingMutation.mutate()}
                    disabled={pingMutation.isPending}
                    className="inline-flex items-center space-x-1 px-4 py-2 bg-purple-50 text-[#6D3FE0] border border-[#6D3FE0] rounded-lg text-xs font-semibold hover:bg-purple-100 disabled:opacity-50"
                  >
                    <Zap className="w-3.5 h-3.5" />
                    <span>{pingMutation.isPending ? 'Pinging...' : 'Ping LLM'}</span>
                  </button>
                </div>

                <p className="text-xs text-[#6B7280]">
                  Pinging executes a 1-token test probe against the active model (
                  {llmStatus?.model || 'OpenAI'}). A tiny provider charge will be incurred. Results
                  are cached for 60s.
                </p>

                {pingMutation.data && (
                  <div
                    className={cn(
                      'p-3 rounded-lg text-xs font-medium border',
                      pingMutation.data.status === 'ok'
                        ? 'bg-green-50 text-green-900 border-green-200'
                        : 'bg-red-50 text-red-900 border-red-200'
                    )}
                  >
                    <div className="font-bold">
                      Status: {pingMutation.data.status.toUpperCase()} (
                      {pingMutation.data.latency_ms}ms)
                    </div>
                    <div>{pingMutation.data.message}</div>
                  </div>
                )}
              </div>

              {/* Cache Stats & Management */}
              <div className="card-base p-6 space-y-6">
                <div className="flex items-center justify-between">
                  <h2 className="text-base font-bold text-[#111827] flex items-center space-x-2">
                    <Database className="w-5 h-5 text-[#6D3FE0]" />
                    <span>Multi-Tier Cache Statistics</span>
                  </h2>

                  <div className="flex items-center space-x-2">
                    <button
                      type="button"
                      onClick={() => refetchCache()}
                      className="p-1.5 text-gray-500 hover:text-gray-900 border rounded-md"
                      title="Refresh stats"
                    >
                      <RefreshCw className="w-4 h-4" />
                    </button>
                    <button
                      type="button"
                      onClick={() => setClearDialogOpen(true)}
                      className="inline-flex items-center space-x-1.5 px-3 py-1.5 bg-red-600 text-white rounded-lg text-xs font-semibold hover:bg-red-700 shadow-sm"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                      <span>Clear Cache</span>
                    </button>
                  </div>
                </div>

                {cacheLoading ? (
                  <div className="h-24 bg-gray-100 rounded-lg animate-pulse" />
                ) : cacheStats ? (
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-xs">
                    <div className="p-3 bg-gray-50 rounded-lg border">
                      <div className="text-gray-500">Total Requests</div>
                      <div className="text-lg font-bold text-gray-900 mt-1">
                        {cacheStats.total_requests}
                      </div>
                    </div>
                    <div className="p-3 bg-gray-50 rounded-lg border">
                      <div className="text-gray-500">L1 Hit Ratio</div>
                      <div className="text-lg font-bold text-gray-900 mt-1">
                        {(cacheStats.l1_hit_ratio * 100).toFixed(1)}%
                      </div>
                    </div>
                    <div className="p-3 bg-gray-50 rounded-lg border">
                      <div className="text-gray-500">L2 SQL Hits / Misses</div>
                      <div className="text-lg font-bold text-gray-900 mt-1">
                        {cacheStats.l2_sql_hits} / {cacheStats.l2_sql_misses}
                      </div>
                    </div>
                    <div className="p-3 bg-gray-50 rounded-lg border">
                      <div className="text-gray-500">L2 SAP Hits / Misses</div>
                      <div className="text-lg font-bold text-gray-900 mt-1">
                        {cacheStats.l2_sap_hits} / {cacheStats.l2_sap_misses}
                      </div>
                    </div>
                    <div className="p-3 bg-gray-50 rounded-lg border">
                      <div className="text-gray-500">L3 Similarity Reuses</div>
                      <div className="text-lg font-bold text-gray-900 mt-1">
                        {cacheStats.l3_similarity_reuses}
                      </div>
                    </div>
                    <div className="p-3 bg-gray-50 rounded-lg border">
                      <div className="text-gray-500">Estimated Tokens Saved</div>
                      <div className="text-lg font-bold text-green-700 mt-1">
                        {cacheStats.estimated_tokens_saved.toLocaleString()}
                      </div>
                    </div>
                    <div className="p-3 bg-gray-50 rounded-lg border">
                      <div className="text-gray-500">Estimated Cost Saved</div>
                      <div className="text-lg font-bold text-green-700 mt-1">
                        ${cacheStats.estimated_cost_saved_usd.toFixed(2)}
                      </div>
                    </div>
                    <div className="p-3 bg-gray-50 rounded-lg border">
                      <div className="text-gray-500">Backend Type</div>
                      <div className="text-lg font-bold text-gray-900 mt-1 capitalize">
                        {cacheStats.backend_type}
                      </div>
                    </div>
                  </div>
                ) : null}
              </div>
            </Tabs.Content>

            {/* TAB 2: LEARNED SAP KNOWLEDGE */}
            <Tabs.Content value="learned" className="space-y-4">
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
                <div>
                  <h2 className="text-base font-bold text-[#111827]">
                    Learned SAP Table Knowledge
                  </h2>
                  <p className="text-xs text-[#6B7280]">
                    Review and approve grounded table/field associations retrieved from vector
                    search.
                  </p>
                </div>

                <div className="flex items-center space-x-2">
                  <span className="text-xs text-gray-600">Filter status:</span>
                  <select
                    value={statusFilter}
                    onChange={(e) =>
                      setStatusFilter(e.target.value as 'all' | 'pending' | 'approved' | 'rejected')
                    }
                    className="text-xs border border-gray-300 rounded px-2.5 py-1.5 bg-white font-medium"
                  >
                    <option value="all">All</option>
                    <option value="pending">Pending</option>
                    <option value="approved">Approved</option>
                    <option value="rejected">Rejected</option>
                  </select>
                </div>
              </div>

              {learnedLoading ? (
                <div className="h-40 bg-gray-100 rounded-lg animate-pulse" />
              ) : learnedSap?.length === 0 ? (
                <div className="card-base p-8 text-center text-gray-500 text-sm">
                  No learned knowledge entries matching filter.
                </div>
              ) : (
                <div className="space-y-3">
                  {learnedSap?.map((entry) => (
                    <div
                      key={entry.id || entry.table + entry.field}
                      className="card-base p-4 space-y-2 text-xs flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4"
                    >
                      <div className="space-y-1 max-w-2xl">
                        <div className="flex items-center space-x-2">
                          <span className="font-mono font-bold text-sm text-[#111827]">
                            {entry.table}
                            {entry.field ? `.${entry.field}` : ''}
                          </span>
                          <span
                            className={cn(
                              'px-2 py-0.5 rounded text-[10px] font-bold uppercase',
                              entry.status === 'approved'
                                ? 'bg-green-100 text-green-800'
                                : entry.status === 'rejected'
                                  ? 'bg-red-100 text-red-800'
                                  : 'bg-yellow-100 text-yellow-800'
                            )}
                          >
                            {entry.status}
                          </span>
                          <span className="text-gray-400">
                            Confidence: {(entry.confidence * 100).toFixed(0)}%
                          </span>
                        </div>
                        <p className="text-gray-700 whitespace-pre-wrap">{entry.description}</p>
                        {entry.business_meaning && (
                          <p className="text-gray-500 italic whitespace-pre-wrap">
                            Meaning: {entry.business_meaning}
                          </p>
                        )}
                        <div className="text-[10px] text-gray-400">
                          Source file: {entry.evidence?.source_file} (Page{' '}
                          {entry.evidence?.page ?? '—'})
                        </div>
                      </div>

                      {entry.id && (
                        <div className="flex items-center space-x-2 flex-shrink-0">
                          <button
                            type="button"
                            onClick={() => setConfirmAction({ type: 'approve', entry })}
                            className="px-3 py-1 bg-green-600 text-white rounded text-xs font-semibold hover:bg-green-700 flex items-center space-x-1"
                          >
                            <Check className="w-3.5 h-3.5" />
                            <span>Approve</span>
                          </button>
                          <button
                            type="button"
                            onClick={() => setConfirmAction({ type: 'reject', entry })}
                            className="px-3 py-1 bg-red-600 text-white rounded text-xs font-semibold hover:bg-red-700 flex items-center space-x-1"
                          >
                            <X className="w-3.5 h-3.5" />
                            <span>Reject</span>
                          </button>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </Tabs.Content>

            {/* TAB 3: GLOSSARY PROPOSALS */}
            <Tabs.Content value="glossary" className="space-y-4">
              <div>
                <h2 className="text-base font-bold text-[#111827]">
                  Proposed Governance Glossary Terms
                </h2>
                <p className="text-xs text-[#6B7280]">
                  Read-only audit record of newly synthesized subject areas, business processes, and
                  concepts.
                </p>
              </div>

              {proposalsLoading ? (
                <div className="h-40 bg-gray-100 rounded-lg animate-pulse" />
              ) : proposals?.length === 0 ? (
                <div className="card-base p-8 text-center text-gray-500 text-sm">
                  No taxonomy proposals pending review.
                </div>
              ) : (
                <div className="card-base p-4 overflow-x-auto">
                  <table className="w-full text-left text-xs">
                    <thead>
                      <tr className="border-b border-[#E5E7EB] text-[#6B7280]">
                        <th className="py-2 px-3 font-semibold">Type</th>
                        <th className="py-2 px-3 font-semibold">Name / Term</th>
                        <th className="py-2 px-3 font-semibold">Status</th>
                        <th className="py-2 px-3 font-semibold">Details</th>
                      </tr>
                    </thead>
                    <tbody>
                      {proposals?.map((item, idx) => (
                        <tr key={idx} className="border-b border-gray-100 hover:bg-gray-50">
                          <td className="py-2 px-3 font-medium capitalize">
                            {String(item.type || 'term')}
                          </td>
                          <td className="py-2 px-3 font-bold text-gray-900">
                            {String(item.name || item.term || '—')}
                          </td>
                          <td className="py-2 px-3">
                            <span className="px-2 py-0.5 rounded text-[10px] bg-purple-100 text-purple-800 font-semibold">
                              {String(item.status || 'proposed')}
                            </span>
                          </td>
                          <td className="py-2 px-3 text-gray-600 max-w-sm truncate">
                            {String(item.description || item.parent || '—')}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Tabs.Content>
          </Tabs.Root>
        </div>
      )}

      {/* Clear Cache Confirmation Dialog */}
      <Dialog.Root open={clearDialogOpen} onOpenChange={setClearDialogOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50" />
          <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-full max-w-md bg-white rounded-xl p-6 shadow-2xl border border-[#E5E7EB] z-50 focus:outline-none space-y-4">
            <Dialog.Title className="text-base font-bold text-[#111827] flex items-center space-x-2">
              <AlertTriangle className="w-5 h-5 text-red-600" />
              <span>Confirm Cache Eviction</span>
            </Dialog.Title>

            <p className="text-xs text-gray-600">
              Select the cache tier you want to purge. Purging the cache will require subsequent
              queries to make live LLM requests.
            </p>

            <div>
              <label
                htmlFor="clear-kind"
                className="block text-xs font-semibold text-gray-700 mb-1"
              >
                Target Cache Tier:
              </label>
              <select
                id="clear-kind"
                value={clearKind}
                onChange={(e) => setClearKind(e.target.value as 'all' | 'l1' | 'l2' | 'l3')}
                className="w-full text-xs border border-gray-300 rounded-md p-2 bg-white"
              >
                <option value="all">All Tiers (Complete Purge)</option>
                <option value="l1">L1 Only (Exact Query Hash)</option>
                <option value="l2">L2 Only (Sub-Agent Intermediate)</option>
                <option value="l3">L3 Only (Semantic Similarity)</option>
              </select>
            </div>

            <div className="flex justify-end space-x-2 pt-2">
              <button
                type="button"
                onClick={() => setClearDialogOpen(false)}
                className="px-3 py-1.5 border rounded text-xs font-medium text-gray-600 hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => clearMutation.mutate(clearKind)}
                disabled={clearMutation.isPending}
                className="px-4 py-1.5 bg-red-600 text-white rounded text-xs font-semibold hover:bg-red-700 disabled:opacity-50"
              >
                {clearMutation.isPending ? 'Clearing...' : 'Confirm Purge'}
              </button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>

      {/* Curate Confirm Dialog */}
      <Dialog.Root
        open={Boolean(confirmAction)}
        onOpenChange={(open) => !open && setConfirmAction(null)}
      >
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50" />
          <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-full max-w-sm bg-white rounded-xl p-5 shadow-2xl border border-[#E5E7EB] z-50 focus:outline-none space-y-3">
            <Dialog.Title className="text-sm font-bold text-[#111827] capitalize">
              Confirm {confirmAction?.type} Entry
            </Dialog.Title>

            <p className="text-xs text-gray-600">
              Are you sure you want to {confirmAction?.type} the learned association for{' '}
              <span className="font-mono font-bold">{confirmAction?.entry.table}</span>?
            </p>

            <div className="flex justify-end space-x-2 pt-2">
              <button
                type="button"
                onClick={() => setConfirmAction(null)}
                className="px-3 py-1.5 border rounded text-xs font-medium text-gray-600 hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => {
                  if (confirmAction?.entry.id) {
                    curateMutation.mutate({
                      id: confirmAction.entry.id,
                      action: confirmAction.type,
                    });
                  }
                }}
                disabled={curateMutation.isPending}
                className={cn(
                  'px-4 py-1.5 text-white rounded text-xs font-semibold disabled:opacity-50',
                  confirmAction?.type === 'approve'
                    ? 'bg-green-600 hover:bg-green-700'
                    : 'bg-red-600 hover:bg-red-700'
                )}
              >
                Confirm
              </button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
};

export default AdminPage;
