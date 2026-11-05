import React from 'react';
import { cn } from '@/lib/utils';

export interface PageHeaderProps {
  title: string;
  subtitle?: string;
  action?: React.ReactNode;
  className?: string;
}

export const PageHeader: React.FC<PageHeaderProps> = ({ title, subtitle, action, className }) => {
  return (
    <div
      className={cn(
        'flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 pb-2',
        className
      )}
    >
      <div>
        <h1 className="text-3xl sm:text-4xl font-extrabold tracking-tight gradient-text">
          {title}
        </h1>
        {subtitle && <p className="mt-1 text-sm sm:text-base text-[#6B7280]">{subtitle}</p>}
      </div>
      {action && <div className="flex-shrink-0">{action}</div>}
    </div>
  );
};

export default PageHeader;
