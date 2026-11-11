"""MongoDB maintenance confirmation and result dialogs."""
from bson import Binary

from opennavicat.mongodb_collection_maintenance import CollectionMaintenanceResult
from opennavicat.ui.mongo_maintenance import MongoMaintenanceConfirmDialog, MongoMaintenanceResultDialog


def test_maintenance_confirmation_requires_explicit_ack(qtbot):
    dialog = MongoMaintenanceConfirmDialog(
        '验证集合', 'db.docs', 'db.runCommand({validate: "docs", full: true})',
        'full validation can be slow', '验证')
    qtbot.addWidget(dialog)
    accepted = []
    dialog.accepted.connect(lambda: accepted.append(True))
    assert not dialog.confirm.isEnabled()
    dialog.ack.setChecked(True)
    assert dialog.confirm.isEnabled()
    dialog.accept()
    assert accepted == [True]


def test_maintenance_result_renders_bounded_report(qtbot):
    result = CollectionMaintenanceResult(
        'validate', Binary(b'\0' * 16, 4), {'ok': 1.0, 'valid': True, 'nrecords': 3, 'nIndexes': 2},
        4.25, 3, 2)
    dialog = MongoMaintenanceResultDialog(result)
    qtbot.addWidget(dialog)
    assert dialog.windowTitle() == '集合验证结果'
    assert '"valid": true' in dialog.report.toPlainText()
    assert '"$numberInt": "3"' in dialog.report.toPlainText()
