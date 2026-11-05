import React, { useState, useEffect } from 'react';
import { Link2, Check, AlertCircle, RotateCcw, Info } from 'lucide-react';
import { PageHeader } from '@/components/PageHeader';
import { skpConnectionStore, validateSkpUrl } from './skpConnectionStore';

export const SKPConnectionsPage: React.FC = () => {
  const [urlInput, setUrlInput] = useState('');
  const [savedUrl, setSavedUrl] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [showSavedNotification, setShowSavedNotification] = useState(false);

  useEffect(() => {
    const existing = skpConnectionStore.getSkpUrl();
    if (existing) {
      setSavedUrl(existing);
      setUrlInput(existing);
    }
  }, []);

  const handleSave = (e: React.FormEvent) => {
    e.preventDefault();
    const res = validateSkpUrl(urlInput);
    if (!res.valid || !res.normalizedUrl) {
      setErrorMsg(res.error || 'Invalid URL');
      return;
    }

    try {
      const stored = skpConnectionStore.saveSkpUrl(res.normalizedUrl);
      setSavedUrl(stored);
      setUrlInput(stored);
      setErrorMsg(null);
      setShowSavedNotification(true);
      setTimeout(() => setShowSavedNotification(false), 4000);
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : 'Failed to save');
    }
  };

  const handleReset = () => {
    skpConnectionStore.reset();
    setSavedUrl(null);
    setUrlInput('');
    setErrorMsg(null);
    setShowSavedNotification(false);
  };

  return (
    <div className="space-y-8 pb-16">
      <PageHeader
        title="SKP Connections"
        subtitle="Configure the SKP connection used to extract metadata"
      />

      <div className="card-base p-6 sm:p-8 max-w-3xl space-y-6">
        <div className="flex items-center space-x-3 pb-4 border-b border-[#E5E7EB]">
          <div className="w-10 h-10 rounded-lg bg-violet-50 text-[#6D3FE0] flex items-center justify-center flex-shrink-0">
            <Link2 className="w-5 h-5" aria-hidden="true" />
          </div>
          <div>
            <h2 className="text-base font-bold text-[#111827]">SKP Service Endpoint</h2>
            <p className="text-xs sm:text-sm text-[#6B7280]">
              The base URL of the SKP metadata extractor server used to scan and catalog ERP assets.
            </p>
          </div>
        </div>

        {/* Saved confirmation badge */}
        {showSavedNotification && (
          <div
            role="status"
            className="p-3 bg-emerald-50 border border-emerald-200 text-emerald-900 rounded-lg text-xs font-semibold flex items-center space-x-2 animate-in fade-in"
          >
            <Check className="w-4 h-4 text-emerald-600 flex-shrink-0" />
            <span>Connection URL saved successfully: {savedUrl}</span>
          </div>
        )}

        <form onSubmit={handleSave} className="space-y-4">
          <div>
            <label
              htmlFor="skp-url"
              className="block text-xs font-bold text-[#111827] uppercase tracking-wider mb-1"
            >
              SKP URL <span className="text-red-500">*</span>
            </label>
            <input
              id="skp-url"
              type="url"
              required
              value={urlInput}
              onChange={(e) => {
                setUrlInput(e.target.value);
                if (errorMsg) setErrorMsg(null);
              }}
              placeholder="https://skp.internal.company.com"
              aria-describedby={errorMsg ? 'skp-url-error skp-url-helper' : 'skp-url-helper'}
              aria-invalid={Boolean(errorMsg)}
              className="w-full px-4 py-2.5 text-sm bg-white border border-[#E5E7EB] rounded-lg focus:outline-none focus:ring-2 focus:ring-[#6D3FE0] placeholder-[#9CA3AF]"
            />
            <p id="skp-url-helper" className="text-xs text-[#6B7280] mt-1.5">
              Must be http:// or https:// (HTTPS required for remote endpoints; HTTP allowed only
              for localhost / 127.0.0.1). Embedded credentials are not allowed.
            </p>

            {errorMsg && (
              <div
                id="skp-url-error"
                role="alert"
                className="mt-2 p-2.5 bg-red-50 border border-red-200 rounded-lg text-xs text-red-800 font-medium flex items-center space-x-2"
              >
                <AlertCircle className="w-4 h-4 text-red-600 flex-shrink-0" />
                <span>{errorMsg}</span>
              </div>
            )}
          </div>

          <div className="flex items-center justify-between pt-4 border-t border-[#E5E7EB]">
            <button
              type="button"
              onClick={handleReset}
              disabled={!savedUrl && !urlInput}
              className="px-4 py-2 border border-[#E5E7EB] text-[#4B5563] hover:bg-gray-50 rounded-lg text-xs font-semibold disabled:opacity-50 disabled:cursor-not-allowed focus-visible:ring-2 focus-visible:ring-[#6D3FE0] flex items-center space-x-1.5"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              <span>Reset</span>
            </button>

            <button
              type="submit"
              className="inline-flex items-center space-x-1.5 px-5 py-2.5 rounded-lg text-xs sm:text-sm font-semibold text-white gradient-brand hover:opacity-90 shadow-sm focus-visible:ring-2 focus-visible:ring-[#6D3FE0] transition-opacity"
            >
              <Check className="w-4 h-4" />
              <span>Save Connection</span>
            </button>
          </div>
        </form>

        <div className="p-3.5 bg-gray-50 border border-gray-200 rounded-xl flex items-start space-x-2.5 text-xs text-[#6B7280]">
          <Info className="w-4 h-4 text-[#6D3FE0] flex-shrink-0 mt-0.5" />
          <p>
            Saved in this browser only until backend integration. Never store authorization
            credentials or secret tokens in this URL or configuration.
          </p>
        </div>
      </div>
    </div>
  );
};

export default SKPConnectionsPage;
