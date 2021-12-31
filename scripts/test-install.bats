@test "Bats is installed" {
    exit 0
}

@test "Install wheel to virtualenv $SERVICE_UNIT_NAME" {
    run python3.7 -m virtualenv ${SERVICE_UNIT_NAME}
    run ${SERVICE_UNIT_NAME}/bin/pip install dist/*.whl
    test -f ${SERVICE_UNIT_NAME}/bin/tarpn-node
}