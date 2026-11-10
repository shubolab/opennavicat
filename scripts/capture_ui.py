"""Render owned fixtures through actual application widgets for UI comparison.

This does not read or drive the installed reference application. Its reference
counterparts are observed separately through CUA, using the same SQLite fixture.
"""
from __future__ import annotations
import argparse
import json
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from opennavicat.database import ConnectionConfig
from opennavicat.storage import Store, create_sample
from opennavicat.ui.dialogs import ConnectionDialog
from opennavicat.ui.theme import apply_theme
from opennavicat.ui.window import MainWindow, QueryPage, TablePage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--theme', choices=['dark', 'light', 'system'], default='dark')
    parser.add_argument('--output', type=Path, default=root / 'artifacts/ui-reference/current')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv[:1])
    app.setApplicationName('OpenNavicat')
    apply_theme(app, args.theme)
    temporary = tempfile.TemporaryDirectory(prefix='opennavicat-ui-capture-')
    store = Store(Path(temporary.name))
    fixture = root / 'artifacts/ui-reference/sample-workspace.sqlite3'
    if not fixture.exists():
        fixture.parent.mkdir(parents=True, exist_ok=True)
        create_sample(fixture.parent)
    config = ConnectionConfig('ui-reference', 'OpenNavicat UI Reference', database=str(fixture))
    # An in-memory test profile avoids touching the user's keychain or workspace.
    store.state['connections'] = [dict(config.__dict__, password='')]
    window = MainWindow(store)
    window.resize(1512, 920)
    window.show()
    window.raise_()
    window.activateWindow()
    errors = []
    window.error = lambda exc: errors.append(str(exc))
    state = {'stage': 0, 'ticks': 0}
    metrics = {}
    def grab(name):
        window.grab().save(str(output / f'{name}.png'))
    def step():
        state['ticks'] += 1
        if state['ticks'] > 400 or errors:
            print({'capture_errors': errors, 'state': state}, file=sys.stderr)
            app.exit(1)
            return
        if window.tasks or window.pool.activeThreadCount():
            QTimer.singleShot(30, step)
            return
        stage = state['stage']
        if stage == 0:
            grab('disconnected')
            window.set_active(config.id)
            window.connect_id(config.id)
        elif stage == 1:
            window.show_objects('table')
        elif stage == 2:
            page = window.object_page
            for row in range(page.table.rowCount()):
                if page.table.item(row, 0).text() == 'customers':
                    page.table.setCurrentCell(row, 0)
                    break
            grab('objects')
            metrics['window'] = [window.width(), window.height()]
            metrics['frame'] = [window.frameGeometry().width(), window.frameGeometry().height()]
            metrics['device_pixel_ratio'] = window.devicePixelRatioF()
            metrics['reference_frame_pixels'] = [3024, 1898]
            metrics['sidebar_width'] = window.sidebar.width()
            metrics['main_toolbar_height'] = window.main_toolbar.height()
            window.open_table({'id': config.id, 'schema': 'main', 'table': 'customers', 'kind': 'table'})
        elif stage == 3:
            page = window.tabs.currentWidget()
            if not isinstance(page, TablePage) or page.model is None or page.loading:
                QTimer.singleShot(30, step)
                return
            grab('table')
            window.new_query(sql='SELECT id, name, email, city FROM customers;', connection_id=config.id)
        elif stage == 4:
            grab('query-before-run')
            window.run_current()
        elif stage == 5:
            page = window.tabs.currentWidget()
            if not isinstance(page, QueryPage) or page.running or page.current_result() is None:
                QTimer.singleShot(30, step)
                return
            grab('query-results')
            metrics['query_toolbar_height'] = page.toolbar.height()
            metrics['query_execution_height'] = page.execution_bar.height()
            metrics['query_results'] = {'rows': len(page.current_result().rows), 'columns': page.current_result().columns}
            metrics['result_tabbar_geometry'] = page.results.tabBar().geometry().getRect()
            metrics['result_width'] = page.results.width()
            window.info_action.setChecked(True)
            window.toggle_inspector(True)
        elif stage == 6:
            grab('query-inspector')
            for driver in ('sqlite', 'postgresql'):
                dialog = ConnectionDialog(window)
                dialog.driver_combo.setCurrentIndex(dialog.driver_combo.findData(driver))
                dialog.show()
                app.processEvents()
                dialog.grab().save(str(output / f'connection-{driver}.png'))
                dialog.reject()
                dialog.deleteLater()
            (output / 'metrics.json').write_text(json.dumps(metrics, ensure_ascii=False, indent=2), 'utf-8')
            window.close()
            app.exit(0)
            return
        state['stage'] += 1
        QTimer.singleShot(60, step)
    QTimer.singleShot(100, step)
    result = app.exec()
    temporary.cleanup()
    print(json.dumps({'capture': 'passed' if result == 0 else 'failed', 'output': str(output), 'metrics': metrics}, ensure_ascii=False))
    return result


if __name__ == '__main__':
    raise SystemExit(main())
