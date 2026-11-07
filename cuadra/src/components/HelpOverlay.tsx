import { useStore } from '../store'

const GROUPS: { title: string; keys: [string, string][] }[] = [
  {
    title: 'Mesa de trabajo',
    keys: [
      ['Enter', 'Confirmar y pasar al siguiente'],
      ['1 … 9', 'Marcar o desmarcar la partida candidata'],
      ['↑ ↓ y Espacio', 'Recorrer candidatas y marcar'],
      ['A S D', 'Aplicar una combinación que cuadra'],
      ['F', 'Cambiar a la partida más antigua (FIFO)'],
      ['Tab', 'Editar el importe de cada partida'],
      ['E', 'Elegir excepción'],
      ['G', 'Asignar category'],
      ['U', 'Marcar UNIDENTIFIED y avanzar'],
      ['/', 'Buscar partidas'],
      ['O', 'Abrir el primer PDF relacionado'],
      ['J K o ← →', 'Siguiente o anterior sin guardar'],
    ],
  },
  {
    title: 'Cola de triaje',
    keys: [
      ['J K o ↑ ↓', 'Mover el cursor'],
      ['Enter', 'Abrir en la mesa'],
      ['C', 'Confirmar la propuesta sin abrir'],
      ['Av Pág / Re Pág', 'Cambiar de página'],
      ['/', 'Buscar por tercero o concepto'],
    ],
  },
  {
    title: 'En toda la app',
    keys: [
      ['Alt + 1 2 3 4', 'Panel, cola, mesa, exportar'],
      ['Z', 'Deshacer la última decisión'],
      ['?', 'Mostrar u ocultar esta ayuda'],
    ],
  },
]

export function HelpOverlay() {
  const setHelp = useStore((s) => s.setHelp)
  return (
    <div className="overlay" role="dialog" aria-modal="true" aria-label="Atajos de teclado" onClick={() => setHelp(false)}>
      <div className="help" onClick={(event) => event.stopPropagation()}>
        {GROUPS.map((group) => (
          <section key={group.title}>
            <h2 className="label">{group.title}</h2>
            <dl>
              {group.keys.map(([key, text]) => (
                <div key={key} className="help-row">
                  <dt>
                    <kbd>{key}</kbd>
                  </dt>
                  <dd>{text}</dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
      </div>
    </div>
  )
}
