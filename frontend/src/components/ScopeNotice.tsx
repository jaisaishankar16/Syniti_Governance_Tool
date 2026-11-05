import React from 'react';
import { Link, useParams } from 'react-router-dom';
import { ArrowLeft, Info } from 'lucide-react';

export const ScopeNotice: React.FC = () => {
  const params = useParams();

  return (
    <div className="card-base p-8 max-w-lg mx-auto my-12 text-center space-y-4">
      <div className="w-12 h-12 bg-purple-50 text-[#6D3FE0] rounded-full mx-auto flex items-center justify-center">
        <Info className="w-6 h-6" />
      </div>

      <div className="space-y-2">
        <h1 className="text-xl font-bold text-[#111827]">Feature Scope Notice</h1>
        <p className="text-sm text-[#4B5563] leading-relaxed">
          This view is outside the current build. The Trial section on the home page is the working
          feature.
        </p>
        {params.id && (
          <p className="text-xs text-[#9CA3AF] font-mono">Requested Resource ID: {params.id}</p>
        )}
      </div>

      <div className="pt-4">
        <Link
          to="/"
          className="inline-flex items-center space-x-2 px-4 py-2 gradient-brand text-white text-sm font-semibold rounded-lg hover:opacity-90 shadow-sm focus-visible:ring-2 focus-visible:ring-[#6D3FE0]"
        >
          <ArrowLeft className="w-4 h-4" />
          <span>Back to Home</span>
        </Link>
      </div>
    </div>
  );
};
