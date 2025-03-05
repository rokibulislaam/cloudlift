from cloudlift.config.stack import get_cluster_name, get_service_stack_name


def test_get_cluster_name():
    assert get_cluster_name("staging") == "cluster-staging"


def test_get_service_stack_name():
    assert get_service_stack_name("unicorn", "some-service") == "some-service-unicorn"
    assert get_service_stack_name("staging", "some_service") == "some_service-staging"
