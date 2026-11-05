import React, { useMemo } from 'react';
import CodeMirror, { type ReactCodeMirrorRef } from '@uiw/react-codemirror';
import { sql, MSSQL } from '@codemirror/lang-sql';

export interface SqlEditorProps {
  value: string;
  onChange?: (val: string) => void;
  readOnly?: boolean;
  minHeight?: string;
  placeholder?: string;
  editorRef?: React.Ref<ReactCodeMirrorRef>;
  errorLine?: number | null;
  id?: string;
  'aria-label'?: string;
}

export const SqlEditor: React.FC<SqlEditorProps> = ({
  value,
  onChange,
  readOnly = false,
  minHeight = '260px',
  placeholder = 'Paste T-SQL query or CREATE VIEW statement here...',
  editorRef,
  id,
  'aria-label': ariaLabel = 'SQL Query Input',
}) => {
  const extensions = useMemo(() => {
    return [sql({ dialect: MSSQL })];
  }, []);

  return (
    <div
      className="border border-[#E5E7EB] rounded-lg overflow-hidden bg-white text-sm font-mono focus-within:ring-2 focus-within:ring-[#6D3FE0] resize-y relative"
      style={{ minHeight }}
      id={id}
    >
      <textarea
        aria-label={ariaLabel}
        data-testid="sql-editor-input"
        value={value}
        onChange={(e) => onChange?.(e.target.value)}
        readOnly={readOnly}
        className="sr-only"
        placeholder={placeholder}
      />
      <CodeMirror
        ref={editorRef}
        value={value}
        height="100%"
        minHeight={minHeight}
        extensions={extensions}
        editable={!readOnly}
        readOnly={readOnly}
        placeholder={placeholder}
        onChange={onChange}
        basicSetup={{
          lineNumbers: true,
          highlightActiveLineGutter: !readOnly,
          highlightActiveLine: !readOnly,
          foldGutter: true,
          dropCursor: true,
          allowMultipleSelections: false,
          indentOnInput: true,
          bracketMatching: true,
          closeBrackets: true,
          autocompletion: false,
          rectangularSelection: false,
          crosshairCursor: false,
          highlightSelectionMatches: true,
        }}
        aria-label={ariaLabel || 'SQL Query Input'}
      />
    </div>
  );
};

export default SqlEditor;
