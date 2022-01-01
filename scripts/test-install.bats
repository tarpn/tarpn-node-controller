@test "Install wheel to virtualenv $SERVICE_UNIT_NAME" {
    run python3.7 -m virtualenv ${SERVICE_UNIT_NAME}
    run ${SERVICE_UNIT_NAME}/bin/pip install dist/*.whl
    test -f ${SERVICE_UNIT_NAME}/bin/tarpn-node
}

@test "Patch service file" {
    export INSTALL_DIR=$(readlink -f ${SERVICE_UNIT_NAME})
    run sed -i "s|/opt/tarpn-core|${INSTALL_DIR}|g" scripts/tarpn-core.service
    run sed -i "/^User=/d" scripts/tarpn-core.service
    run cat scripts/tarpn-core.service
}