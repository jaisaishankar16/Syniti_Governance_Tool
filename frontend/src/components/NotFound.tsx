import React from 'react';
import { Link } from 'react-router-dom';
import { Home } from 'lucide-react';

export const NotFound: React.FC = () => {
  return (
    <div className="card-base p-10 max-w-md mx-auto my-16 text-center space-y-4">
      <div className="text-4xl font-extrabold text-[#6D3FE0]">404</div>
      <h1 className="text-xl font-bold text-[#111827]">Page Not Found</h1>
      <p className="text-sm text-[#6B7280]">
        The page you are looking for does not exist or has been moved.
      </p>
      <div className="pt-2">
        <Link
          to="/"
          className="inline-flex items-center space-x-2 px-4 py-2 gradient-brand text-white text-sm font-semibold rounded-lg hover:opacity-90 shadow-sm focus-visible:ring-2 focus-visible:ring-[#6D3FE0]"
        >
          <Home className="w-4 h-4" />
          <span>Return Home</span>
        </Link>
      </div>
    </div>
  );
};
