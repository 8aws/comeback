// ─── i18n — Spanish / English ────────────────────────────────────────────────
// Canonical keys are the literals used in HTML/JS. Each entry: [es, en].
// The DOM is translated in place (text nodes keep their original on __orig),
// and a MutationObserver re-translates dynamically rendered content.

const I18N = {
  // nav & header
  '📦 Backup': ['📦 Backup', '📦 Backup'],
  '🔄 Restore': ['🔄 Restaurar', '🔄 Restore'],
  '🚀 Deploy': ['🚀 Desplegar', '🚀 Deploy'],
  '⬆️ Updates': ['⬆️ Updates', '⬆️ Updates'],
  '📊 Monitor': ['📊 Monitor', '📊 Monitor'],
  '📋 Jobs': ['📋 Tareas', '📋 Jobs'],

  // backup tab
  '🐳 Containers': ['🐳 Contenedores', '🐳 Containers'],
  'All': ['Todo', 'All'],
  'None': ['Nada', 'None'],
  '↻ Refresh': ['↻ Actualizar', '↻ Refresh'],
  'Todos': ['Todos', 'All'],
  'Activos': ['Activos', 'Running'],
  'Detenidos': ['Detenidos', 'Stopped'],
  '⭐ Favoritos': ['⭐ Favoritos', '⭐ Favorites'],
  'Columnas': ['Columnas', 'Columns'],
  'A → Z': ['A → Z', 'A → Z'],
  'Más recientes': ['Más recientes', 'Newest first'],
  'Mayor tamaño': ['Mayor tamaño', 'Largest first'],
  'Cargando contenedores…': ['Cargando contenedores…', 'Loading containers…'],
  '⚙️ Backup Options': ['⚙️ Opciones de backup', '⚙️ Backup Options'],
  'Label (optional)': ['Etiqueta (opcional)', 'Label (optional)'],
  'Save Docker images': ['Guardar imágenes Docker', 'Save Docker images'],
  'Embeds images in the archive (larger, fully portable)': ['Incluye las imágenes en el archivo (más grande, totalmente portable)', 'Embeds images in the archive (larger, fully portable)'],
  '📦 What gets backed up': ['📦 Qué se incluye en el backup', '📦 What gets backed up'],
  '📦 Start Backup': ['📦 Iniciar backup', '📦 Start Backup'],
  '⏰ Programar': ['⏰ Programar', '⏰ Schedule'],
  '⏰ Backups programados': ['⏰ Backups programados', '⏰ Scheduled backups'],
  'Nombre': ['Nombre', 'Name'],
  'Frecuencia': ['Frecuencia', 'Frequency'],
  'Diaria': ['Diaria', 'Daily'],
  'Semanal': ['Semanal', 'Weekly'],
  'Hora': ['Hora', 'Time'],
  'Día de la semana': ['Día de la semana', 'Day of week'],
  'Lunes': ['Lunes', 'Monday'], 'Martes': ['Martes', 'Tuesday'], 'Miércoles': ['Miércoles', 'Wednesday'],
  'Jueves': ['Jueves', 'Thursday'], 'Viernes': ['Viernes', 'Friday'], 'Sábado': ['Sábado', 'Saturday'], 'Domingo': ['Domingo', 'Sunday'],
  'Retención (copias a mantener)': ['Retención (copias a mantener)', 'Retention (copies to keep)'],
  'Cancelar': ['Cancelar', 'Cancel'],
  '✓ Crear': ['✓ Crear', '✓ Create'],
  'Sin programaciones': ['Sin programaciones', 'No schedules'],
  'Sin programaciones. Selecciona contenedores y pulsa ⏰ Programar.': ['Sin programaciones. Selecciona contenedores y pulsa ⏰ Programar.', 'No schedules. Select containers and press ⏰ Schedule.'],
  '⚠️ Con problemas': ['⚠️ Con problemas', '⚠️ With problems'],
  '🐳 Resto': ['🐳 Resto', '🐳 Others'],
  '🐳 Contenedores': ['🐳 Contenedores', '🐳 Containers'],
  'Nada que mostrar con este filtro.': ['Nada que mostrar con este filtro.', 'Nothing to show with this filter.'],
  'verificado': ['verificado', 'verified'],
  'Click para quitar el verificado': ['Click para quitar el verificado', 'Click to remove the verified mark'],
  'Marcar como verificado — se gestiona como contenedor normal': ['Marcar como verificado — se gestiona como contenedor normal', 'Mark as verified — handled as a normal container'],
  'Favorito': ['Favorito', 'Favorite'],

  // restore tab
  '📤 Importar backup': ['📤 Importar backup', '📤 Import backup'],
  '📤 Seleccionar archivo .tar.gz': ['📤 Seleccionar archivo .tar.gz', '📤 Select .tar.gz file'],
  '📦 Available Backups': ['📦 Backups disponibles', '📦 Available Backups'],
  '🧪 Test Containers & Volumes': ['🧪 Contenedores y volúmenes de prueba', '🧪 Test Containers & Volumes'],
  'No hay backups': ['No hay backups', 'No backups found'],
  'Crea un backup desde la pestaña Backup': ['Crea un backup desde la pestaña Backup', 'Create a backup from the Backup tab'],
  'copias': ['copias', 'copies'],
  'contenedores': ['contenedores', 'containers'],
  'volúmenes': ['volúmenes', 'volumes'],
  'bases de datos': ['bases de datos', 'databases'],
  'Verify': ['Verificar', 'Verify'],
  'Download': ['Descargar', 'Download'],
  'Select containers to restore': ['Selecciona contenedores a restaurar', 'Select containers to restore'],
  'Options': ['Opciones', 'Options'],
  'Remove existing containers': ['Eliminar contenedores existentes', 'Remove existing containers'],
  'Start containers after restore': ['Arrancar contenedores tras restaurar', 'Start containers after restore'],
  'Cancel': ['Cancelar', 'Cancel'],
  '🔄 Start Restore': ['🔄 Iniciar restauración', '🔄 Start Restore'],

  // deploy tab
  '🛠 Despliegue personalizado': ['🛠 Despliegue personalizado', '🛠 Custom deploy'],
  'Nombre del despliegue': ['Nombre del despliegue', 'Deploy name'],
  'Nombre del contenedor': ['Nombre del contenedor', 'Container name'],
  'Tag de la imagen': ['Tag de la imagen', 'Image tag'],
  'Variables de entorno ': ['Variables de entorno ', 'Environment variables '],
  'Puertos ': ['Puertos ', 'Ports '],
  'Restart policy': ['Política de reinicio', 'Restart policy'],
  '🚀 Desplegar': ['🚀 Desplegar', '🚀 Deploy'],
  '🚀 Build & Deploy': ['🚀 Build & Deploy', '🚀 Build & Deploy'],

  // updates tab
  '⬆️ Actualizaciones de imágenes': ['⬆️ Actualizaciones de imágenes', '⬆️ Image updates'],
  '↻ Comprobar': ['↻ Comprobar', '↻ Check'],
  '📦 Hacer backup antes de actualizar': ['📦 Hacer backup antes de actualizar', '📦 Back up before updating'],
  'Consultando registries…': ['Consultando registries…', 'Querying registries…'],
  'No hay contenedores.': ['No hay contenedores.', 'No containers.'],
  '⏳ comprobando…': ['⏳ comprobando…', '⏳ checking…'],
  'Actualización disponible': ['Actualización disponible', 'Update available'],
  'Al día': ['Al día', 'Up to date'],
  'Fijada por digest': ['Fijada por digest', 'Pinned by digest'],
  'Imagen local': ['Imagen local', 'Local image'],
  'Desconocido': ['Desconocido', 'Unknown'],
  '⬆️ Actualizar': ['⬆️ Actualizar', '⬆️ Update'],
  '⬇ Solo pull': ['⬇ Solo pull', '⬇ Pull only'],

  // monitor tab
  '📊 Monitor de recursos': ['📊 Monitor de recursos', '📊 Resource monitor'],
  'Mayor CPU': ['Mayor CPU', 'Highest CPU'],
  'Mayor RAM': ['Mayor RAM', 'Highest RAM'],
  'Mayor red': ['Mayor red', 'Highest network'],
  'Mayor disco': ['Mayor disco', 'Highest disk'],
  '⭐ Favoritos primero': ['⭐ Favoritos primero', '⭐ Favorites first'],
  'No hay contenedores en ejecución.': ['No hay contenedores en ejecución.', 'No running containers.'],
  'desactivado': ['desactivado', 'disabled'],
  '⏸ Monitoreo desactivado — actívalo en ⚙️ Ajustes.': ['⏸ Monitoreo desactivado — actívalo en ⚙️ Ajustes.', '⏸ Monitoring disabled — enable it in ⚙️ Settings.'],

  // jobs
  '📋 Job History': ['📋 Historial de tareas', '📋 Job History'],
  'Progress': ['Progreso', 'Progress'],
  '✓ OK': ['✓ OK', '✓ OK'],

  // settings
  '⚙️ Ajustes': ['⚙️ Ajustes', '⚙️ Settings'],
  'Versión': ['Versión', 'Version'],
  'Instancia': ['Instancia', 'Instance'],
  'Tema': ['Tema', 'Theme'],
  '☀️ Claro': ['☀️ Claro', '☀️ Light'],
  '🌙 Oscuro': ['🌙 Oscuro', '🌙 Dark'],
  '💻 Sistema': ['💻 Sistema', '💻 System'],
  'Idioma': ['Idioma', 'Language'],
  '🌐 Auto': ['🌐 Auto', '🌐 Auto'],
  'Monitoreo activo': ['Monitoreo activo', 'Monitoring enabled'],
  'Sondea CPU/RAM/red cada 5 s mientras la pestaña Monitor está abierta': ['Sondea CPU/RAM/red cada 5 s mientras la pestaña Monitor está abierta', 'Polls CPU/RAM/network every 5s while the Monitor tab is open'],
  'Cambiar contraseña': ['Cambiar contraseña', 'Change password'],
  '🔑 Cambiar': ['🔑 Cambiar', '🔑 Change'],
  '🚪 Cerrar sesión': ['🚪 Cerrar sesión', '🚪 Log out'],
  'Las contraseñas nuevas no coinciden': ['Las contraseñas nuevas no coinciden', 'New passwords do not match'],

  // container actions
  'Forzar pull de la imagen': ['Forzar pull de la imagen', 'Force image pull'],
  'Pausar': ['Pausar', 'Pause'],
  'Reanudar': ['Reanudar', 'Resume'],
  'Arrancar': ['Arrancar', 'Start'],
  'Parar': ['Parar', 'Stop'],
  'Reiniciar': ['Reiniciar', 'Restart'],
  'Recrear (misma config)': ['Recrear (misma config)', 'Recreate (same config)'],
  'Kill (SIGKILL)': ['Kill (SIGKILL)', 'Kill (SIGKILL)'],

  // deploy environment
  '🧭 Entorno del servidor': ['🧭 Entorno del servidor', '🧭 Server environment'],
  'Cargando entorno…': ['Cargando entorno…', 'Loading environment…'],
  'Comprobar puerto…': ['Comprobar puerto…', 'Check port…'],
  '🔍 Comprobar': ['🔍 Comprobar', '🔍 Check'],
  'Puertos ocupados': ['Puertos ocupados', 'Ports in use'],
  'ninguno': ['ninguno', 'none'],
  'Rutas de datos usadas por otros contenedores': ['Rutas de datos usadas por otros contenedores', 'Data paths used by other containers'],
  'Redes compartidas': ['Redes compartidas', 'Shared networks'],
  'ocupado por': ['ocupado por', 'in use by'],
  'un proceso del host': ['un proceso del host', 'a host process'],
  'disponible': ['disponible', 'available'],
  '🚀 Plantillas de despliegue': ['🚀 Plantillas de despliegue', '🚀 Deploy templates'],
  '🎹 MelodY — Generador visual de Compose': ['🎹 MelodY — Generador visual de Compose', '🎹 MelodY — Visual Compose generator'],

  // login
  'Entrar': ['Entrar', 'Sign in'],
  'Usuario': ['Usuario', 'Username'],
  'Contraseña': ['Contraseña', 'Password'],
  'Contraseña actual': ['Contraseña actual', 'Current password'],
  'Nueva contraseña (mín. 8)': ['Nueva contraseña (mín. 8)', 'New password (min. 8)'],
  'Repite la nueva contraseña': ['Repite la nueva contraseña', 'Repeat the new password'],
  'Error de conexión': ['Error de conexión', 'Connection error'],
};

function currentLang() {
  const pref = localStorage.getItem('cb_lang') || 'auto';
  if (pref === 'auto') {
    return (navigator.language || 'en').toLowerCase().startsWith('es') ? 'es' : 'en';
  }
  return pref;
}

function t(key) {
  const entry = I18N[key];
  if (!entry) return key;
  return currentLang() === 'en' ? entry[1] : entry[0];
}

// ─── DOM translation ─────────────────────────────────────────────────────────

function _translateTextNode(node) {
  if (node.__orig === undefined) node.__orig = node.textContent;
  const trimmed = node.__orig.trim();
  const entry = I18N[trimmed];
  if (!entry) return;
  const target = currentLang() === 'en' ? entry[1] : entry[0];
  node.textContent = node.__orig.replace(trimmed, target);
}

function _translateAttrs(el) {
  for (const attr of ['placeholder', 'title']) {
    if (!el.hasAttribute || !el.hasAttribute(attr)) continue;
    const store = `__orig_${attr}`;
    if (el[store] === undefined) el[store] = el.getAttribute(attr);
    const entry = I18N[el[store]];
    if (entry) el.setAttribute(attr, currentLang() === 'en' ? entry[1] : entry[0]);
  }
}

function translateTree(root) {
  if (!root) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
  let n = walker.currentNode;
  while (n) {
    if (n.nodeType === Node.TEXT_NODE) _translateTextNode(n);
    else if (n.nodeType === Node.ELEMENT_NODE) _translateAttrs(n);
    n = walker.nextNode();
  }
}

let _i18nObserver = null;

function applyLang() {
  translateTree(document.body);
  if (!_i18nObserver) {
    _i18nObserver = new MutationObserver(muts => {
      for (const m of muts) {
        m.addedNodes.forEach(node => {
          if (node.nodeType === Node.TEXT_NODE) _translateTextNode(node);
          else if (node.nodeType === Node.ELEMENT_NODE) translateTree(node);
        });
      }
    });
    _i18nObserver.observe(document.body, { childList: true, subtree: true });
  }
  document.documentElement.lang = currentLang();
  document.querySelectorAll('.lang-btn').forEach(b =>
    b.classList.toggle('btn-primary', b.dataset.langPref === (localStorage.getItem('cb_lang') || 'auto')));
}

function setLang(pref) {
  localStorage.setItem('cb_lang', pref);
  applyLang();
}
