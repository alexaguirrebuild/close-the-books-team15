import { useStore } from '../store'
import { useFocusTrap } from './useFocusTrap'

const GROUPS: { title: string; keys: [string[], string][] }[] = [
  {
    title: 'Decidir',
    keys: [
      [['Enter'], 'Confirmar y seguir. Si falta excepción, la pide'],
      [['Shift', 'Enter'], 'Guardar sin excepción'],
      [['1 … 9'], 'Marcar o desmarcar una partida'],
      [['↑', '↓', 'Espacio'], 'Recorrer partidas y marcar'],
      [['A', 'S', 'D'], 'Aplicar una combinación que cuadra'],
      [['F'], 'Cambiar a la partida más antigua'],
      [['Tab'], 'Editar importes'],
      [['E'], 'Elegir excepción'],
      [['G'], 'Asignar categoría, sin partida'],
      [['U'], 'Marcar sin identificar y seguir'],
    ],
  },
  {
    title: 'Moverse',
    keys: [
      [['J', 'K'], 'Movimiento siguiente o anterior; el borrador se conserva'],
      [['Av Pág', 'Re Pág'], 'Página siguiente o anterior'],
      [['V'], 'Cambiar de vista'],
      [['B'], 'Buscar movimiento'],
      [['/'], 'Buscar partida'],
      [['M'], 'Ver más partidas del tercero'],
      [['L'], 'Ocultar o mostrar la lista'],
      [['O'], 'Abrir o cerrar el documento'],
      [['Shift', 'C'], 'Confirmar en lote, en la vista Listas'],
    ],
  },
  {
    title: 'En toda la app',
    keys: [
      [['Alt', '1 2 3'], 'Resumen, Revisión, Validar'],
      [['Z'], 'Deshacer la última decisión'],
      [['?'], 'Mostrar u ocultar esta ayuda'],
      [['Esc'], 'Cerrar lo que esté abierto'],
    ],
  },
]

export function HelpOverlay() {
  const setHelp = useStore((s) => s.setHelp)
  const trapRef = useFocusTrap<HTMLDivElement>()
  return (
    <div className="overlay" role="dialog" aria-modal="true" aria-label="Atajos de teclado" onClick={() => setHelp(false)}>
      <div className="help" ref={trapRef} tabIndex={-1} onClick={(event) => event.stopPropagation()}>
        {GROUPS.map((group) => (
          <section key={group.title}>
            <h2>{group.title}</h2>
            <dl>
              {group.keys.map(([keys, text]) => (
                <div key={text} className="help-row">
                  <dt>{keys.map((key) => <kbd key={key}>{key}</kbd>)}</dt>
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
