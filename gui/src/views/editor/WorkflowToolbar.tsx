export function WorkflowToolbar({
  name,
  libraryOpen,
  inspectorOpen,
  disabled,
  onNameChange,
  onToggleLibrary,
  onToggleInspector,
  onArrange,
}: {
  name: string
  libraryOpen: boolean
  inspectorOpen: boolean
  disabled: boolean
  onNameChange: (name: string) => void
  onToggleLibrary: () => void
  onToggleInspector: () => void
  onArrange: () => void
}) {
  return (
    <header className="workflow-canvas-toolbar" aria-label="流程画布工具栏">
      <button
        type="button"
        className="editor-button workflow-library-toggle"
        aria-controls="workflow-library"
        aria-expanded={libraryOpen}
        onClick={onToggleLibrary}
      >
        {libraryOpen ? '收起目录' : '打开目录'}
      </button>
      <label className="workflow-name-field">
        <span>工作流名称</span>
        <input
          aria-label="工作流名称"
          className="editor-input"
          value={name}
          disabled={disabled}
          maxLength={128}
          onChange={event => onNameChange(event.target.value)}
        />
      </label>
      <button type="button" className="editor-button" disabled={disabled} onClick={onArrange}>整理布局</button>
      <button
        type="button"
        className="editor-button workflow-inspector-toggle"
        aria-controls="workflow-step-inspector"
        aria-expanded={inspectorOpen}
        onClick={onToggleInspector}
      >
        {inspectorOpen ? '流程画布' : '步骤详情'}
      </button>
    </header>
  )
}
