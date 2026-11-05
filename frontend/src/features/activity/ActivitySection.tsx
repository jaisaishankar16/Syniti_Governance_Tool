import React from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Activity,
  Table2,
  CheckCircle2,
  Clock,
  XCircle,
  AlertTriangle,
  AlertCircle,
  RefreshCw,
} from 'lucide-react';
import { apiFetch } from '@/api/client';
import { ActivityListSchema, type ActivityItem } from '@/api/schemas';

export const ActivitySection: React.FC = () => {
  const {
    data: activities,
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery<ActivityItem[]>({
    queryKey: ['activity'],
    queryFn: () =>
      apiFetch<ActivityItem[]>('/api/v1/activity?limit=20', { schema: ActivityListSchema }),
    staleTime: 30_000,
  });

  return (
    <section className="space-y-4" aria-labelledby="activity-heading">
      <div>
        <div className="flex items-center space-x-2">
          <Activity className="w-5 h-5 text-[#6D3FE0]" aria-hidden="true" />
          <h2 id="activity-heading" className="text-xl font-bold text-[#111827]">
            Recent Activity
          </h2>
        </div>
        <p className="text-xs sm:text-sm text-[#6B7280] mt-0.5">
          Latest validation actions on target tables across all projects
        </p>
      </div>

      {/* Skeletons */}
      {isLoading && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4" aria-busy="true">
          {[1, 2, 3, 4].map((n) => (
            <div key={n} className="card-base p-4 space-y-3 animate-pulse">
              <div className="flex justify-between items-center">
                <div className="w-20 h-5 bg-gray-200 rounded-full" />
                <div className="w-24 h-5 bg-gray-200 rounded" />
              </div>
              <div className="w-1/2 h-5 bg-gray-200 rounded" />
              <div className="w-3/4 h-4 bg-gray-200 rounded" />
            </div>
          ))}
        </div>
      )}

      {/* Error state */}
      {isError && (
        <div className="card-base p-6 bg-red-50 border-red-200 text-red-900 flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <AlertCircle className="w-6 h-6 text-red-600 flex-shrink-0" />
            <div>
              <h3 className="font-semibold text-sm">Failed to load recent activity</h3>
              <p className="text-xs text-red-700 mt-0.5">
                {error instanceof Error ? error.message : 'Please check connection.'}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => refetch()}
            className="px-3 py-1.5 bg-red-600 text-white rounded-md text-xs font-semibold hover:bg-red-700 flex items-center space-x-1"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            <span>Retry</span>
          </button>
        </div>
      )}

      {/* Empty state: exact text required */}
      {!isLoading && !isError && (!activities || activities.length === 0) && (
        <div className="card-base p-8 text-center text-[#6B7280]">
          <p className="font-semibold text-sm text-[#111827]">
            No Recent Activity yet - execute to see the latest details
          </p>
        </div>
      )}

      {/* Activity Grid (2 columns) */}
      {!isLoading && !isError && activities && activities.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {activities.map((item) => {
            const formattedTime = (() => {
              try {
                const date = new Date(item.finished_at);
                return isNaN(date.getTime())
                  ? item.finished_at
                  : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
              } catch {
                return item.finished_at;
              }
            })();

            return (
              <div
                key={item.id}
                className="card-base p-4 flex items-center justify-between hover:border-[#6D3FE0]/40 transition-colors"
              >
                <div className="space-y-2 flex-1 pr-4 min-w-0">
                  {/* Badges row */}
                  <div className="flex items-center space-x-2 flex-wrap gap-y-1">
                    {/* Status Chip */}
                    {item.status === 'complete' && (
                      <span className="inline-flex items-center space-x-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-[#DCFCE7] text-[#15803D]">
                        <CheckCircle2 className="w-3 h-3 text-[#22C55E]" />
                        <span>Complete</span>
                      </span>
                    )}
                    {item.status === 'degraded' && (
                      <span className="inline-flex items-center space-x-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-amber-50 text-amber-800 border border-amber-200">
                        <AlertTriangle className="w-3 h-3 text-amber-600" />
                        <span>Degraded</span>
                      </span>
                    )}
                    {item.status === 'failed' && (
                      <span className="inline-flex items-center space-x-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-[#FEE2E2] text-[#B91C1C]">
                        <XCircle className="w-3 h-3 text-[#EF4444]" />
                        <span>Failed</span>
                      </span>
                    )}

                    {/* Run Type Chip */}
                    <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-[#F3F4F6] text-[#4B5563]">
                      {item.run_type}
                    </span>
                  </div>

                  {/* Table/View Name */}
                  <div className="flex items-center space-x-2">
                    <Table2 className="w-4 h-4 text-[#6B7280] flex-shrink-0" />
                    <span className="font-bold text-sm text-[#111827] truncate">
                      {item.table_name}
                    </span>
                  </div>

                  {/* Subtitle */}
                  <p className="text-xs text-[#6B7280] truncate">{item.subtitle}</p>
                </div>

                {/* Finished Time */}
                <div className="flex-shrink-0 text-right">
                  <span className="text-xs font-medium text-[#6B7280] flex items-center space-x-1">
                    <Clock className="w-3 h-3 text-[#9CA3AF]" />
                    <span>{formattedTime}</span>
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
};

export default ActivitySection;
