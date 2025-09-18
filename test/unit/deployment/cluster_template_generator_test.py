import json

import boto3
import botocore.stub
import pytest
from cfn_flip import to_json
from stringcase import camelcase
from troposphere import Parameter, Ref, Template
from troposphere.ecs import Cluster
from troposphere.ec2 import SecurityGroup
from troposphere.elasticloadbalancingv2 import LoadBalancer as ALBLoadBalancer

from cloudlift.deployment.cluster_template_generator import ClusterTemplateGenerator
from cloudlift.utils import generate_pascalcase_name

TEST_REGION = "ap-south-1"
TEST_NOTIFICATIONS_ARN = f"arn:aws:sns:{TEST_REGION}:123456789012:test-notifications"
TEST_ACM_ARN = f"arn:aws:acm:{TEST_REGION}:123456789012:certificate/test-cert"
TEST_ENV_NAME = "test-env"


@pytest.fixture
@pytest.mark.parametrize(
    "mock_get_region_for_environment", [TEST_REGION], indirect=True
)
@pytest.mark.parametrize(
    "mock_get_notifications_arn_for_environment",
    [TEST_NOTIFICATIONS_ARN],
    indirect=True,
)
@pytest.mark.parametrize(
    "mock_get_ssl_certification_for_environment", [TEST_ACM_ARN], indirect=True
)
def basic_cluster_config(
    mock_get_notifications_arn_for_environment,
    mock_get_ssl_certification_for_environment,
    mock_get_region_for_environment,
    monkeypatch,
):
    """Basic cluster configuration with minimal required fields."""
    return {
        "vpc": {
            "cidr": "10.0.0.0/16",
            "subnets": {
                "public": {
                    "subnet-1": {"cidr": "10.0.1.0/24"},
                    "subnet-2": {"cidr": "10.0.2.0/24"},
                },
                "private": {
                    "subnet-1": {"cidr": "10.0.3.0/24"},
                    "subnet-2": {"cidr": "10.0.4.0/24"},
                },
            },
            "nat-gateway": {"elastic-ip-allocation-id": "eipalloc-12345678"},
        },
        "cluster": {
            "min_instances": 1,
            "max_instances": 5,
            "instance_type": "t2.micro",
            "key_name": "test-key",
            "ami_id": None,
        },
        "environment": {
            "notifications_arn": TEST_NOTIFICATIONS_ARN,
            "ssl_certificate_arn": TEST_ACM_ARN,
        },
    }


CUSTOM_AMI_ID_SSM_PATH = "/custom/path/to/ami"
CUSTOM_AMI_ID = "custom-ami"


@pytest.fixture
def mock_ssm_ami_id_parameters(request, monkeypatch, mock_aws):
    """
    Mock AMI IDParameter Store operations for testing.

    This fixture is necessary because AWS SSM has reserved parameter paths that cannot
    be modified, even in test environments. for example
    - /aws/*           - Reserved for AWS service use
    - /amazon/*        - Reserved for Amazon service use

    Specifically, the ECS optimized AMI path ('/aws/service/ecs/optimized-ami/*')
    is a reserved path that AWS uses to provide the latest ECS-optimized AMI IDs.
    Attempting to use put_parameter() directly on these paths will result in
    "AccessDeniedException: No access to reserved parameter name", this holds true even if we try to
    put_parameter() to moto's mock AWS environment.

    To work around this limitation in tests:
    1. We use botocore's Stubber instead of direct moto mocking
    2. Stubber intercepts the API calls before they reach AWS/moto
    3. This allows us to "mock" operations on reserved paths that would
       otherwise be rejected

    Args:
        request: The pytest request object containing indirect parameters
        mock_aws: The AWS mocking fixture (provided by moto)

    Yields:
        boto3.client: A stubbed SSM client that will return mocked responses
    """
    OriginalSession = boto3.session.Session

    # Create a client to base our stubber on
    original_client = boto3.client("ssm")
    stubber = botocore.stub.Stubber(original_client)

    # Get expected responses from indirect parameters, or use defaults
    default_ami_response = {
        "/aws/service/ecs/optimized-ami/amazon-linux-2023/recommended": {
            "image_id": "ami-default123",
            "name": "amzn2-ami-ecs-hvm",
        },
        CUSTOM_AMI_ID_SSM_PATH: {"image_id": CUSTOM_AMI_ID},
    }
    expected_responses = getattr(request, "param", default_ami_response)

    # Add all expected responses to the stubber
    for path, response_data in expected_responses.items():
        stubber.add_response(
            "get_parameter",
            {
                "Parameter": {
                    "Name": path,
                    "Type": "String",
                    "Value": json.dumps(response_data),
                    "Version": 1,
                }
            },
            {"Name": path},
        )

    class MockSession(OriginalSession):
        """Mock Session that returns our stubbed client for SSM"""

        def client(self, service_name, *args, **kwargs):
            if service_name == "ssm":
                return original_client
            return super().client(service_name, *args, **kwargs)

    # Patch the Session
    monkeypatch.setattr("boto3.session.Session", MockSession)
    monkeypatch.setattr(
        "boto3.client",
        lambda service_name, *args, **kwargs: (
            original_client
            if service_name == "ssm"
            else OriginalSession().client(service_name, *args, **kwargs)
        ),
    )

    stubber.activate()
    yield
    stubber.deactivate()


@pytest.fixture(autouse=True)
def mock_describe_availability_zones(monkeypatch):
    """
    Mock describe_availability_zones call for testing.
    """
    ec2_client = boto3.client("ec2")
    stubber = botocore.stub.Stubber(ec2_client)

    response = {
        "AvailabilityZones": [
            {"ZoneName": f"{TEST_REGION}a"},
            {"ZoneName": f"{TEST_REGION}b"},
            {"ZoneName": f"{TEST_REGION}c"},
        ]
    }

    stubber.add_response("describe_availability_zones", response)

    class MockSession(boto3.session.Session):
        def client(self, service_name, *args, **kwargs):
            if service_name == "ec2":
                return ec2_client
            return super().client(service_name, *args, **kwargs)

    monkeypatch.setattr("boto3.session.Session", MockSession)
    monkeypatch.setattr(
        "boto3.client",
        lambda service_name, *args, **kwargs: (
            ec2_client
            if service_name == "ec2"
            else boto3.session.Session().client(service_name, *args, **kwargs)
        ),
    )

    stubber.activate()
    yield
    stubber.deactivate()


@pytest.fixture
def cluster_template_generator(basic_cluster_config, mock_describe_availability_zones):
    """ClusterTemplateGenerator fixture with basic configuration."""
    return ClusterTemplateGenerator(TEST_ENV_NAME, basic_cluster_config)


def test_basic_initialization(mock_aws, basic_cluster_config):
    """Test ClusterTemplateGenerator initializes correctly with basic config."""
    generator = ClusterTemplateGenerator(TEST_ENV_NAME, basic_cluster_config)

    assert isinstance(generator.template, Template)
    assert generator.env == TEST_ENV_NAME
    assert generator.cluster_name == "cluster-test-env"
    assert generator.configuration == basic_cluster_config
    assert len(generator.private_subnets) == 0  # Not yet populated
    assert len(generator.public_subnets) == 0  # Not yet populated
    assert generator.desired_instances is None


def test_initialization_with_spot_config(mock_aws, basic_cluster_config):
    """Test initialization with spot instance configuration."""
    basic_cluster_config["cluster"].update(
        {
            "spot_min_instances": 2,
            "spot_max_instances": 4,
            "allocation_strategy": "capacity-optimized",
        }
    )

    generator = ClusterTemplateGenerator(TEST_ENV_NAME, basic_cluster_config)

    assert generator.configuration["cluster"]["spot_min_instances"] == 2
    assert generator.configuration["cluster"]["spot_max_instances"] == 4
    assert (
        generator.configuration["cluster"]["allocation_strategy"]
        == "capacity-optimized"
    )


def test_initialization_with_desired_instances(mock_aws, basic_cluster_config):
    """Test initialization with specified desired instances."""
    generator = ClusterTemplateGenerator(
        TEST_ENV_NAME, basic_cluster_config, desired_instances=3
    )

    assert generator.desired_instances == 3
    assert generator.configuration["cluster"]["min_instances"] == 1
    assert generator.configuration["cluster"]["max_instances"] == 5


def test_initialization_default_spot_values(mock_aws, basic_cluster_config):
    """Test default spot configuration values are set when not provided."""
    generator = ClusterTemplateGenerator(TEST_ENV_NAME, basic_cluster_config)
    assert generator.configuration["cluster"]["spot_min_instances"] == 0
    assert generator.configuration["cluster"]["spot_max_instances"] == 0
    assert (
        generator.configuration["cluster"]["allocation_strategy"]
        == "capacity-optimized"
    )


def test_availability_zones_initialization(
    mock_aws, basic_cluster_config, mock_describe_availability_zones
):
    """
    Test availability zones are fetched correctly on initialization.
    """
    generator = ClusterTemplateGenerator(TEST_ENV_NAME, basic_cluster_config)

    assert len(generator.availability_zones) == 2
    assert all(az.startswith(TEST_REGION) for az in generator.availability_zones)
    assert generator.availability_zones[0] != generator.availability_zones[1]


@pytest.mark.parametrize(
    "mock_get_notifications_arn_for_environment",
    [f"arn:aws:sns:{TEST_REGION}:123456789012:test-team"],
    indirect=True,
)
def test_team_name_from_notifications_arn(
    mock_aws, basic_cluster_config, mock_get_notifications_arn_for_environment
):
    """Test team name is correctly extracted from notifications ARN."""
    generator = ClusterTemplateGenerator(TEST_ENV_NAME, basic_cluster_config)
    assert generator.team_name == "test-team"


def test_generate_cluster_template(
    mock_aws,
    cluster_template_generator,
    mock_ssm_ami_id_parameters,
):
    """Test cluster template generation with all components."""
    template_yaml = cluster_template_generator.generate_cluster()
    template_dict = to_json(template_yaml)
    # Test if all the components have been added
    # This is a shallow test just to test if the components were added
    # More thorough tests on resources, mapping etc will be done on their dedicated methods
    # e.g. _setup_network
    assert "Metadata" in template_dict
    assert "Mappings" in template_dict
    assert "Outputs" in template_dict
    assert "Parameters" in template_dict
    assert "Resources" in template_dict


@pytest.fixture
def network_config():
    """Fixture providing network configuration for testing."""
    return {
        "vpc": {
            "cidr": "10.0.0.0/16",
            "nat-gateway": {"elastic-ip-allocation-id": "eipalloc-12345678"},
            "subnets": {
                "public": {
                    "subnet-1": {"cidr": "10.0.1.0/24"},
                    "subnet-2": {"cidr": "10.0.2.0/24"},
                },
                "private": {
                    "subnet-1": {"cidr": "10.0.3.0/24"},
                    "subnet-2": {"cidr": "10.0.4.0/24"},
                },
            },
        }
    }


def test_create_vpc(
    cluster_template_generator,
    network_config,
    mock_aws,
    mock_describe_availability_zones,
):
    """
    Test VPC creation with basic configuration including internet gateway attachment.
    """
    cluster_template_generator._create_vpc(network_config["vpc"]["cidr"])

    template_dict = cluster_template_generator.template.to_dict()
    resources = template_dict["Resources"]

    # Check VPC
    vpc_resource = resources[cluster_template_generator.vpc.title]
    assert vpc_resource["Type"] == "AWS::EC2::VPC"
    assert vpc_resource["Properties"]["CidrBlock"] == "10.0.0.0/16"
    assert vpc_resource["Properties"]["EnableDnsSupport"] is True
    assert vpc_resource["Properties"]["EnableDnsHostnames"] is True

    # Check Internet Gateway
    ig_resource = resources[cluster_template_generator.internet_gateway.title]
    assert ig_resource["Type"] == "AWS::EC2::InternetGateway"

    # Check Gateway Attachment
    vpc_attachment = next(
        r for r in resources.values() if r["Type"] == "AWS::EC2::VPCGatewayAttachment"
    )
    assert (
        vpc_attachment["Properties"]["VpcId"]["Ref"]
        == cluster_template_generator.vpc.title
    )
    assert (
        vpc_attachment["Properties"]["InternetGatewayId"]["Ref"]
        == cluster_template_generator.internet_gateway.title
    )


def test_create_public_network(cluster_template_generator, network_config, mock_aws):
    """
    Test public subnet creation with route tables and internet gateway routes.
    """
    cluster_template_generator._create_vpc(network_config["vpc"]["cidr"])
    cluster_template_generator._create_public_network(
        network_config["vpc"]["subnets"]["public"]
    )

    template_dict = cluster_template_generator.template.to_dict()
    resources = template_dict["Resources"]

    # Check public subnets
    public_subnets = [s for s in resources.values() if s["Type"] == "AWS::EC2::Subnet"]
    assert len(public_subnets) == 2

    for subnet in public_subnets:
        assert subnet["Properties"]["MapPublicIpOnLaunch"] is True
        assert (
            subnet["Properties"]["VpcId"]["Ref"] == cluster_template_generator.vpc.title
        )
        assert any(
            tag["Key"] == "Name" and "public" in tag["Value"]
            for tag in subnet["Properties"]["Tags"]
        )

    # Check route table
    route_table = next(
        r for r in resources.values() if r["Type"] == "AWS::EC2::RouteTable"
    )
    assert (
        route_table["Properties"]["VpcId"]["Ref"]
        == cluster_template_generator.vpc.title
    )

    # Check route table associations
    associations = [
        r
        for r in resources.values()
        if r["Type"] == "AWS::EC2::SubnetRouteTableAssociation"
    ]
    assert len(associations) == 2


def test_create_private_network(
    cluster_template_generator,
    network_config,
    mock_aws,
    mock_describe_availability_zones,
):
    """
    Test private subnet creation with NAT gateway and route tables.
    """
    cluster_template_generator._create_vpc(network_config["vpc"]["cidr"])
    cluster_template_generator._create_public_network(
        network_config["vpc"]["subnets"]["public"]
    )
    cluster_template_generator._create_private_network(
        network_config["vpc"]["subnets"]["private"],
        network_config["vpc"]["nat-gateway"]["elastic-ip-allocation-id"],
    )

    template_dict = cluster_template_generator.template.to_dict()
    resources = template_dict["Resources"]

    # Check private subnets
    private_subnets = [
        s
        for s in resources.values()
        if s["Type"] == "AWS::EC2::Subnet"
        and any(
            tag["Key"] == "Name" and "private" in tag["Value"]
            for tag in s["Properties"]["Tags"]
        )
    ]
    assert len(private_subnets) == 2

    for subnet in private_subnets:
        assert subnet["Properties"]["MapPublicIpOnLaunch"] is False
        assert (
            subnet["Properties"]["VpcId"]["Ref"] == cluster_template_generator.vpc.title
        )

    # Check NAT Gateway
    nat_gateway = next(
        r for r in resources.values() if r["Type"] == "AWS::EC2::NatGateway"
    )
    assert nat_gateway["Properties"]["AllocationId"] == "eipalloc-12345678"

    # Check private route table and routes
    private_route_table = next(
        r
        for r in resources.values()
        if r["Type"] == "AWS::EC2::RouteTable"
        and any(
            tag["Key"] == "Name" and "private" in tag["Value"]
            for tag in r["Properties"]["Tags"]
        )
    )
    assert (
        private_route_table["Properties"]["VpcId"]["Ref"]
        == cluster_template_generator.vpc.title
    )


def test_create_database_subnet_group(
    cluster_template_generator,
    network_config,
    mock_aws,
    mock_describe_availability_zones,
):
    """
    Test database subnet group creation with private subnets.
    """
    cluster_template_generator._create_vpc(network_config["vpc"]["cidr"])
    cluster_template_generator._create_public_network(
        network_config["vpc"]["subnets"]["public"]
    )
    cluster_template_generator._create_private_network(
        network_config["vpc"]["subnets"]["private"],
        network_config["vpc"]["nat-gateway"]["elastic-ip-allocation-id"],
    )
    cluster_template_generator._create_database_subnet_group()

    template_dict = cluster_template_generator.template.to_dict()
    resources = template_dict["Resources"]

    # Check DB Subnet Group
    db_subnet_group = resources["DBSubnetGroup"]
    assert db_subnet_group["Type"] == "AWS::RDS::DBSubnetGroup"
    assert db_subnet_group["Properties"]["DBSubnetGroupName"] == "test-env-subnet"
    assert (
        db_subnet_group["Properties"]["DBSubnetGroupDescription"]
        == "test-env subnet group"
    )
    assert len(db_subnet_group["Properties"]["SubnetIds"]) == 2

    # Check ElastiCache Subnet Group
    elasticache_subnet_group = resources["ElasticacheSubnetGroup"]
    assert elasticache_subnet_group["Type"] == "AWS::ElastiCache::SubnetGroup"
    assert (
        elasticache_subnet_group["Properties"]["CacheSubnetGroupName"]
        == "test-env-subnet"
    )
    assert (
        elasticache_subnet_group["Properties"]["Description"] == "test-env subnet group"
    )
    assert len(elasticache_subnet_group["Properties"]["SubnetIds"]) == 2


def test_create_log_group(cluster_template_generator):
    """
    Test log group creation.
    """
    cluster_template_generator._create_log_group()

    template_dict = cluster_template_generator.template.to_dict()
    resources = template_dict["Resources"]
    log_group_resource_name = camelcase(f"{TEST_ENV_NAME}LogGroup")
    log_group = resources[log_group_resource_name]
    assert log_group["Type"] == "AWS::Logs::LogGroup"
    assert log_group["Properties"]["LogGroupName"] == f"{TEST_ENV_NAME}-logs"
    assert log_group["Properties"]["RetentionInDays"] == 365


def test_setup_cloudmap(cluster_template_generator, mock_aws):
    """
    Test the setup of CloudMap namespace.
    """
    cluster_template_generator._create_vpc("10.0.0.0/16")
    cluster_template_generator._setup_cloudmap()

    template_dict = cluster_template_generator.template.to_dict()
    resources = template_dict["Resources"]

    # Check CloudMap namespace
    cloudmap_resource = resources[cluster_template_generator.cloudmap.title]
    assert cloudmap_resource["Type"] == "AWS::ServiceDiscovery::PrivateDnsNamespace"
    assert cloudmap_resource["Properties"]["Name"] == {"Ref": "AWS::StackName"}
    assert cloudmap_resource["Properties"]["Vpc"] == {
        "Ref": cluster_template_generator.vpc.title
    }

    # Check CloudMap tags
    expected_tags = [
        {"Key": "category", "Value": "services"},
        {"Key": "environment", "Value": TEST_ENV_NAME},
        {"Key": "Team", "Value": cluster_template_generator.team_name},
        {"Key": "Name", "Value": {"Ref": "AWS::StackName"}},
    ]
    assert sorted(
        cloudmap_resource["Properties"]["Tags"], key=lambda x: x["Key"]
    ) == sorted(expected_tags, key=lambda x: x["Key"])


@pytest.mark.parametrize(
    "cluster_config",
    [
        {
            "cluster": {
                "min_instances": 1,
                "max_instances": 5,
                "spot_min_instances": 0,
                "spot_max_instances": 0,
                "instance_type": "t2.micro",
                "key_name": "test-key",
                "ami_id": None,
            }
        },
        {
            "cluster": {
                "min_instances": 2,
                "max_instances": 10,
                "spot_min_instances": 1,
                "spot_max_instances": 5,
                "instance_type": "t3.micro",
                "key_name": "test-key-2",
                "ami_id": None,
            }
        },
        {
            "cluster": {
                "min_instances": 0,
                "max_instances": 0,
                "spot_min_instances": 3,
                "spot_max_instances": 6,
                "instance_type": "t3a.micro",
                "key_name": "test-key-3",
                "ami_id": None,
            }
        },
    ],
)
def test_add_cluster_outputs(cluster_template_generator, cluster_config, mock_aws):
    """
    Test the addition of cluster outputs with various configurations.
    """
    cluster_template_generator.configuration = cluster_config
    cluster_template_generator._create_vpc("10.0.0.0/16")
    cluster_template_generator._create_public_network(
        {
            "subnet-1": {"cidr": "10.0.1.0/24"},
            "subnet-2": {"cidr": "10.0.2.0/24"},
        }
    )
    cluster_template_generator._create_private_network(
        {
            "subnet-1": {"cidr": "10.0.3.0/24"},
            "subnet-2": {"cidr": "10.0.4.0/24"},
        },
        "eipalloc-12345678",
    )
    cluster_template_generator._setup_cloudmap()
    cluster_template_generator._add_cluster_outputs()

    template_dict = cluster_template_generator.template.to_dict()
    outputs = template_dict["Outputs"]

    # Check common outputs
    assert "CloudliftOptions" in outputs
    assert "VPC" in outputs
    assert "PrivateSubnet1" in outputs
    assert "PrivateSubnet2" in outputs
    assert "PublicSubnet1" in outputs
    assert "PublicSubnet2" in outputs
    assert "SecurityGroupAlb" in outputs
    assert "MinInstances" in outputs
    assert "MaxInstances" in outputs
    assert "SpotMinInstances" in outputs
    assert "SpotMaxInstances" in outputs
    assert "InstanceTypes" in outputs
    assert "KeyName" in outputs
    assert "CloudmapId" in outputs
    assert "SecurityGroupEC2Host" in outputs

    # Check specific outputs based on configuration
    if cluster_config["cluster"]["spot_min_instances"] > 0:
        assert "AutoScalingGroupSpot" in outputs
    if cluster_config["cluster"]["min_instances"] > 0:
        assert "AutoScalingGroupOnDemand" in outputs
    if "ecs_instance_default_lifecycle_type" in cluster_config["cluster"]:
        assert "ECSClusterDefaultInstanceLifecycle" in outputs


@pytest.mark.parametrize(
    "cluster_type_config",
    [
        # Test case 1: On-demand only configuration
        {
            "cluster": {
                "min_instances": 2,
                "max_instances": 4,
                "spot_min_instances": 0,
                "spot_max_instances": 0,
                "instance_type": "t3.micro",
                "key_name": "test-key",
                "ami_id": None,
            }
        },
        # Test case 2: Spot only configuration with lowest-price strategy
        {
            "cluster": {
                "min_instances": 0,
                "max_instances": 0,
                "spot_min_instances": 2,
                "spot_max_instances": 5,
                "instance_type": "t3.micro",
                "key_name": "test-key",
                "ami_id": None,
                "allocation_strategy": "lowest-price",
                "spot_instance_pools": 3,
            }
        },
        # Test case 3: Mixed fleet with capacity-optimized strategy
        {
            "cluster": {
                "min_instances": 1,
                "max_instances": 3,
                "spot_min_instances": 2,
                "spot_max_instances": 4,
                "instance_type": "t3.micro,t3.small,t3.medium",
                "key_name": "test-key",
                "ami_id": None,
                "allocation_strategy": "capacity-optimized",
            }
        },
    ],
    indirect=True,
)
def test_add_cluster_with_variations(
    mock_aws, mock_ssm_ami_id_parameters, basic_cluster_config, cluster_type_config
):
    """
    Test _add_cluster method with different cluster configurations:
    1. On-demand instances only
    2. Spot instances only
    3. Mixed fleet with both spot and on-demand
    """
    # Update the base configuration with test case specific values
    config = basic_cluster_config.copy()
    config.update(cluster_type_config)

    generator = ClusterTemplateGenerator(TEST_ENV_NAME, config)
    generator._create_vpc(config["vpc"]["cidr"])
    generator._create_public_network(config["vpc"]["subnets"]["public"])
    generator._create_private_network(
        config["vpc"]["subnets"]["private"],
        config["vpc"]["nat-gateway"]["elastic-ip-allocation-id"],
    )
    # Add parameters before creating the cluster
    generator._add_cluster_parameters()
    generator._add_cluster()

    template_dict = generator.template.to_dict()
    resources = template_dict["Resources"]

    # Verify ECS Cluster
    assert "Cluster" in resources
    assert resources["Cluster"]["Type"] == "AWS::ECS::Cluster"

    # Verify IAM Resources
    assert "ECSRole" in resources
    assert "InstanceProfile" in resources

    # Check Auto Scaling Group configurations
    if config["cluster"]["min_instances"] > 0:
        assert "AutoScalingGroupOnDemand" in resources
        asg = resources["AutoScalingGroupOnDemand"]
        assert asg["Properties"]["MinSize"]["Ref"] == "OnDemandMinSize"
        assert asg["Properties"]["MaxSize"]["Ref"] == "OnDemandMaxSize"

    if config["cluster"]["spot_min_instances"] > 0:
        assert "AutoScalingGroupSpot" in resources
        asg = resources["AutoScalingGroupSpot"]
        assert asg["Properties"]["MinSize"]["Ref"] == "SpotMinSize"
        assert asg["Properties"]["MaxSize"]["Ref"] == "SpotMaxSize"

        # Verify spot fleet configuration
        assert "MixedInstancesPolicy" in asg["Properties"]
        instances_distribution = asg["Properties"]["MixedInstancesPolicy"][
            "InstancesDistribution"
        ]
        assert instances_distribution["SpotAllocationStrategy"] == config[
            "cluster"
        ].get("allocation_strategy", "capacity-optimized")


@pytest.mark.parametrize(
    "cluster_config",
    [
        # Test case 1: On-demand only configuration
        {
            "cluster": {
                "min_instances": 2,
                "max_instances": 4,
                "spot_min_instances": 0,
                "spot_max_instances": 0,
                "instance_type": "t3.micro",
                "key_name": "test-key",
                "ami_id": None,
            }
        },
        # Test case 2: Spot only configuration with lowest-price strategy
        {
            "cluster": {
                "min_instances": 0,
                "max_instances": 0,
                "spot_min_instances": 2,
                "spot_max_instances": 5,
                "instance_type": "t3.micro",
                "key_name": "test-key",
                "ami_id": None,
                "allocation_strategy": "lowest-price",
                "spot_instance_pools": 3,
            }
        },
        # Test case 3: Mixed fleet with capacity-optimized strategy
        {
            "cluster": {
                "min_instances": 1,
                "max_instances": 3,
                "spot_min_instances": 2,
                "spot_max_instances": 4,
                "instance_type": "t3.micro,t3.small,t3.medium",
                "key_name": "test-key",
                "ami_id": None,
                "allocation_strategy": "capacity-optimized",
            }
        },
    ],
)
def test_add_ec2_auto_scaling_configurations(
    mock_aws, mock_ssm_ami_id_parameters, cluster_config, cluster_template_generator
):
    """
    Test _add_ec2_auto_scaling with different configurations.
    Tests:
    1. On-demand only deployment
    2. Spot only deployment with lowest-price strategy
    3. Mixed fleet deployment with capacity-optimized strategy

    Verifies:
    - Launch template creation with correct configurations
    - Auto Scaling Group creation with proper settings
    - Security group creation and rules
    - Proper instance distribution settings
    - Correct metadata and user data content
    """
    cluster_template_generator.configuration = cluster_config
    cluster_template_generator._create_vpc("10.0.0.0/16")
    cluster_template_generator._create_public_network(
        {
            "subnet-1": {"cidr": "10.0.1.0/24"},
            "subnet-2": {"cidr": "10.0.2.0/24"},
        }
    )
    cluster_template_generator._create_private_network(
        {
            "subnet-1": {"cidr": "10.0.3.0/24"},
            "subnet-2": {"cidr": "10.0.4.0/24"},
        },
        "eipalloc-12345678",
    )

    # Setup required parameter references for auto scaling
    key_pair_parameter = Parameter(
        "KeyPair",
        Description="",
        Type="AWS::EC2::KeyPair::KeyName",
        Default=cluster_config["cluster"]["key_name"],
    )
    notification_sns_arn_parameter = Parameter(
        "NotificationSnsArn",
        Description="",
        Type="String",
        Default=cluster_template_generator.notifications_arn,
    )
    cluster_template_generator.key_pair = key_pair_parameter
    cluster_template_generator.notification_sns_arn = notification_sns_arn_parameter

    # Call the method under test
    cluster_template_generator._add_ec2_auto_scaling()

    template_dict = cluster_template_generator.template.to_dict()
    resources = template_dict["Resources"]

    # Verify security groups
    assert "SecurityGroupAlb" in resources
    assert "SecurityGroupEc2Hosts" in resources
    assert "SecurityGroupDatabases" in resources

    # Verify security group rules
    sg_hosts = resources["SecurityGroupEc2Hosts"]
    assert len(sg_hosts["Properties"]["SecurityGroupIngress"]) == 1
    assert (
        sg_hosts["Properties"]["SecurityGroupIngress"][0]["SourceSecurityGroupId"][
            "Ref"
        ]
        == "SecurityGroupAlb"
    )

    # Host security group self-reference ingress rule
    assert "SecurityEc2HostsIngress" in resources
    sg_hosts_ingress = resources["SecurityEc2HostsIngress"]
    assert (
        sg_hosts_ingress["Properties"]["SourceSecurityGroupId"]["Ref"]
        == "SecurityGroupEc2Hosts"
    )
    assert sg_hosts_ingress["Properties"]["GroupId"]["Ref"] == "SecurityGroupEc2Hosts"

    # Check database security group
    sg_db = resources["SecurityGroupDatabases"]
    assert (
        sg_db["Properties"]["SecurityGroupIngress"][0]["SourceSecurityGroupId"]["Ref"]
        == "SecurityGroupEc2Hosts"
    )

    config = cluster_config["cluster"]

    # Test on-demand instances configuration if enabled
    if config["min_instances"] > 0:
        assert "LaunchTemplateOnDemand" in resources
        lt_ondemand = resources["LaunchTemplateOnDemand"]
        assert (
            lt_ondemand["Properties"]["LaunchTemplateName"]
            == f"{TEST_ENV_NAME}-LaunchTemplateOnDemand"
        )

        # Check user data and metadata
        assert "Metadata" in lt_ondemand
        assert "AWS::CloudFormation::Init" in lt_ondemand["Metadata"]

        # Verify the launch template data
        lt_data = lt_ondemand["Properties"]["LaunchTemplateData"]
        assert lt_data["ImageId"]["Fn::FindInMap"] == [
            "AWSRegionToAMI",
            {"Ref": "AWS::Region"},
            "AMI",
        ]
        assert lt_data["KeyName"]["Ref"] == "KeyPair"
        assert (
            lt_data["MetadataOptions"]["HttpTokens"] == "required"
        )  # IMDSv2 is required for security
        assert lt_data["BlockDeviceMappings"][0]["Ebs"]["VolumeType"] == "gp3"

        # Verify Auto Scaling Group
        assert "AutoScalingGroupOnDemand" in resources
        asg_ondemand = resources["AutoScalingGroupOnDemand"]
        assert asg_ondemand["Properties"]["MinSize"]["Ref"] == "OnDemandMinSize"
        assert asg_ondemand["Properties"]["MaxSize"]["Ref"] == "OnDemandMaxSize"

        # Verify mixed instances policy
        assert "MixedInstancesPolicy" in asg_ondemand["Properties"]
        instances_distribution = asg_ondemand["Properties"]["MixedInstancesPolicy"][
            "InstancesDistribution"
        ]
        assert instances_distribution["OnDemandPercentageAboveBaseCapacity"] == 100
        assert instances_distribution["SpotAllocationStrategy"] == "capacity-optimized"

        # Verify notifications
        notifications = asg_ondemand["Properties"]["NotificationConfigurations"]
        assert len(notifications) == 1
        assert notifications[0]["NotificationTypes"] == [
            "autoscaling:EC2_INSTANCE_LAUNCH_ERROR"
        ]
        assert notifications[0]["TopicARN"]["Ref"] == "NotificationSnsArn"

        # Verify alarms
        assert "Ec2HostsHighCPUAlarmOnDemand" in resources
        assert "ClusterHighMemoryReservationAlarmOnDemand" in resources
        assert "AutoScalingPolicyOnDemand" in resources

    # Test spot instances configuration if enabled
    if config["spot_min_instances"] > 0:
        assert "LaunchTemplateSpot" in resources
        lt_spot = resources["LaunchTemplateSpot"]
        assert (
            lt_spot["Properties"]["LaunchTemplateName"]
            == f"{TEST_ENV_NAME}-LaunchTemplateSpot"
        )

        # Check user data and metadata
        lt_data = lt_spot["Properties"]["LaunchTemplateData"]
        assert "UserData" in lt_data

        # Verify Auto Scaling Group
        assert "AutoScalingGroupSpot" in resources
        asg_spot = resources["AutoScalingGroupSpot"]
        assert asg_spot["Properties"]["MinSize"]["Ref"] == "SpotMinSize"
        assert asg_spot["Properties"]["MaxSize"]["Ref"] == "SpotMaxSize"

        # Verify mixed instances policy
        assert "MixedInstancesPolicy" in asg_spot["Properties"]
        instances_distribution = asg_spot["Properties"]["MixedInstancesPolicy"][
            "InstancesDistribution"
        ]
        assert instances_distribution["OnDemandPercentageAboveBaseCapacity"] == 0
        assert instances_distribution["SpotAllocationStrategy"] == config.get(
            "allocation_strategy", "capacity-optimized"
        )

        # Check spot instance pools if allocation strategy is lowest-price
        if (
            config.get("allocation_strategy") == "lowest-price"
            and "spot_instance_pools" in config
        ):
            assert (
                instances_distribution["SpotInstancePools"]
                == config["spot_instance_pools"]
            )

        # Verify instance type overrides for multiple instance types
        instance_types = config["instance_type"].split(",")
        overrides = asg_spot["Properties"]["MixedInstancesPolicy"]["LaunchTemplate"][
            "Overrides"
        ]
        assert len(overrides) == len(instance_types)
        for i, instance_type in enumerate(instance_types):
            assert overrides[i]["InstanceType"] == instance_type


@pytest.mark.parametrize(
    "desired_instances,expected_capacity",
    [
        (None, 1),  # Default to min_instances
        (3, 3),  # Explicit desired capacity
        (5, 5),  # Higher than min but within max
    ],
)
def test_get_desired_capacity(
    mock_aws, basic_cluster_config, desired_instances, expected_capacity
):
    """
    Test _get_desired_capacity method for calculating Auto Scaling Group desired capacity.
    Verifies that the capacity is set correctly based on configuration and override.
    """
    generator = ClusterTemplateGenerator(
        TEST_ENV_NAME, basic_cluster_config, desired_instances
    )

    # Test on-demand desired capacity
    capacity = generator._get_desired_capacity("OnDemand")
    assert capacity == str(
        expected_capacity
        if desired_instances is not None
        else basic_cluster_config["cluster"]["min_instances"]
    )

    # Test spot desired capacity
    capacity = generator._get_desired_capacity("Spot")
    assert capacity == str(
        expected_capacity
        if desired_instances is not None
        else basic_cluster_config["cluster"]["spot_min_instances"]
    )


def test_add_instance_profile(mock_aws, cluster_template_generator):
    """
    Test _add_instance_profile method.
    Verifies that the IAM role and instance profile are created with the correct policies.
    """
    instance_profile = cluster_template_generator._add_instance_profile()

    template_dict = cluster_template_generator.template.to_dict()
    resources = template_dict["Resources"]

    # Verify IAM Role
    assert "ECSRole" in resources
    ecs_role = resources["ECSRole"]
    assert ecs_role["Type"] == "AWS::IAM::Role"
    assert ecs_role["Properties"]["Path"] == "/"
    assert len(ecs_role["Properties"]["ManagedPolicyArns"]) == 3
    assert (
        "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role"
        in ecs_role["Properties"]["ManagedPolicyArns"]
    )
    assert (
        "arn:aws:iam::aws:policy/AmazonDynamoDBReadOnlyAccess"
        in ecs_role["Properties"]["ManagedPolicyArns"]
    )
    assert (
        "arn:aws:iam::aws:policy/service-role/AmazonEC2RoleforSSM"
        in ecs_role["Properties"]["ManagedPolicyArns"]
    )

    # Verify assume role policy
    assume_role = ecs_role["Properties"]["AssumeRolePolicyDocument"]
    assert assume_role["Statement"][0]["Action"] == ["sts:AssumeRole"]
    assert assume_role["Statement"][0]["Effect"] == "Allow"
    assert assume_role["Statement"][0]["Principal"]["Service"] == ["ec2.amazonaws.com"]

    # Verify Instance Profile
    assert "InstanceProfile" in resources
    profile = resources["InstanceProfile"]
    assert profile["Type"] == "AWS::IAM::InstanceProfile"
    assert profile["Properties"]["Path"] == "/"
    assert profile["Properties"]["Roles"] == [{"Ref": "ECSRole"}]

    # Verify instance profile is returned correctly
    assert instance_profile.title == "InstanceProfile"


def test_security_groups_creation(mock_aws, cluster_template_generator):
    """
    Test security group creation in _add_ec2_auto_scaling.
    Verifies the security groups are created with proper configurations.
    """
    cluster_template_generator._create_vpc("10.0.0.0/16")

    # Create public and private subnets needed for _add_ec2_auto_scaling
    cluster_template_generator._create_public_network(
        {
            "subnet-1": {"cidr": "10.0.1.0/24"},
            "subnet-2": {"cidr": "10.0.2.0/24"},
        }
    )

    cluster_template_generator._create_private_network(
        {
            "subnet-1": {"cidr": "10.0.3.0/24"},
            "subnet-2": {"cidr": "10.0.4.0/24"},
        },
        "eipalloc-12345678",
    )

    # Need to set up notification SNS ARN parameter for alarms
    notification_sns_arn_parameter = Parameter(
        "NotificationSnsArn",
        Description="",
        Type="String",
        Default=cluster_template_generator.notifications_arn,
    )
    cluster_template_generator.notification_sns_arn = notification_sns_arn_parameter

    # Initialize key_pair parameter before calling _add_ec2_auto_scaling
    key_pair_parameter = Parameter(
        "KeyPair", Description="", Type="AWS::EC2::KeyPair::KeyName", Default="test-key"
    )
    cluster_template_generator.key_pair = key_pair_parameter

    # Create security groups
    cluster_template_generator._add_ec2_auto_scaling()

    template_dict = cluster_template_generator.template.to_dict()
    resources = template_dict["Resources"]

    # Verify ALB security group
    sg_alb = resources["SecurityGroupAlb"]
    assert sg_alb["Type"] == "AWS::EC2::SecurityGroup"
    # Check that GroupDescription contains the expected Sub function reference
    assert "Fn::Sub" in sg_alb["Properties"]["GroupDescription"]
    assert (
        sg_alb["Properties"]["GroupDescription"]["Fn::Sub"] == "${AWS::StackName}-alb"
    )
    assert sg_alb["Properties"]["VpcId"]["Ref"] == cluster_template_generator.vpc.title

    # Verify EC2 hosts security group
    sg_hosts = resources["SecurityGroupEc2Hosts"]
    assert sg_hosts["Type"] == "AWS::EC2::SecurityGroup"
    assert "Fn::Sub" in sg_hosts["Properties"]["GroupDescription"]
    assert (
        sg_hosts["Properties"]["GroupDescription"]["Fn::Sub"]
        == "${AWS::StackName}-hosts"
    )
    assert (
        sg_hosts["Properties"]["VpcId"]["Ref"] == cluster_template_generator.vpc.title
    )

    # Verify ingress rule allows traffic from ALB
    ingress_rule = sg_hosts["Properties"]["SecurityGroupIngress"][0]
    assert ingress_rule["SourceSecurityGroupId"]["Ref"] == "SecurityGroupAlb"
    assert ingress_rule["IpProtocol"] == -1

    # Verify self-referencing ingress rule for EC2 hosts
    sg_hosts_ingress = resources["SecurityEc2HostsIngress"]
    assert sg_hosts_ingress["Type"] == "AWS::EC2::SecurityGroupIngress"
    assert (
        sg_hosts_ingress["Properties"]["SourceSecurityGroupId"]["Ref"]
        == "SecurityGroupEc2Hosts"
    )
    assert sg_hosts_ingress["Properties"]["GroupId"]["Ref"] == "SecurityGroupEc2Hosts"
    assert sg_hosts_ingress["Properties"]["IpProtocol"] == "-1"

    # Verify database security group
    sg_db = resources["SecurityGroupDatabases"]
    assert sg_db["Type"] == "AWS::EC2::SecurityGroup"
    assert "Fn::Sub" in sg_db["Properties"]["GroupDescription"]
    assert (
        sg_db["Properties"]["GroupDescription"]["Fn::Sub"]
        == "${AWS::StackName}-databases"
    )
    assert sg_db["Properties"]["VpcId"]["Ref"] == cluster_template_generator.vpc.title

    # Verify database security group allows traffic from EC2 hosts
    db_ingress = sg_db["Properties"]["SecurityGroupIngress"][0]
    assert db_ingress["SourceSecurityGroupId"]["Ref"] == "SecurityGroupEc2Hosts"
    assert db_ingress["IpProtocol"] == -1


@pytest.mark.parametrize(
    "instance_types,expected_overrides_count",
    [
        ("t3.micro", 1),  # Single instance type
        ("t3.micro,t3.small", 2),  # Two instance types
        ("t3.micro,t3.small,t3.medium", 3),  # Three instance types
    ],
)
def test_multiple_instance_types_handling(
    mock_aws,
    basic_cluster_config,
    mock_ssm_ami_id_parameters,
    instance_types,
    expected_overrides_count,
):
    """
    Test handling of multiple instance types in launch templates.
    Verifies that the overrides are correctly created for each instance type.
    """
    # Configure for spot instances with multiple instance types
    config = basic_cluster_config.copy()
    config["cluster"].update(
        {
            "min_instances": 0,
            "max_instances": 0,
            "spot_min_instances": 1,
            "spot_max_instances": 3,
            "instance_type": instance_types,
            "key_name": "test-key",
        }
    )

    generator = ClusterTemplateGenerator(TEST_ENV_NAME, config)
    generator._create_vpc("10.0.0.0/16")
    generator._create_public_network(
        {
            "subnet-1": {"cidr": "10.0.1.0/24"},
            "subnet-2": {"cidr": "10.0.2.0/24"},
        }
    )
    generator._create_private_network(
        {
            "subnet-1": {"cidr": "10.0.3.0/24"},
            "subnet-2": {"cidr": "10.0.4.0/24"},
        },
        "eipalloc-12345678",
    )

    # Setup required parameters
    key_pair_parameter = Parameter(
        "KeyPair", Description="", Type="AWS::EC2::KeyPair::KeyName", Default="test-key"
    )
    notification_sns_arn_parameter = Parameter(
        "NotificationSnsArn",
        Description="",
        Type="String",
        Default=generator.notifications_arn,
    )
    generator.key_pair = key_pair_parameter
    generator.notification_sns_arn = notification_sns_arn_parameter

    # Call the method under test
    generator._add_ec2_auto_scaling()

    template_dict = generator.template.to_dict()
    resources = template_dict["Resources"]

    # Verify Spot launch template is created
    assert "LaunchTemplateSpot" in resources

    # Verify Auto Scaling Group for Spot
    assert "AutoScalingGroupSpot" in resources
    asg_spot = resources["AutoScalingGroupSpot"]

    # Verify mixed instances policy
    assert "MixedInstancesPolicy" in asg_spot["Properties"]
    launch_template = asg_spot["Properties"]["MixedInstancesPolicy"]["LaunchTemplate"]

    # Verify instance type overrides
    assert "Overrides" in launch_template
    overrides = launch_template["Overrides"]
    assert len(overrides) == expected_overrides_count

    # Verify each override matches the expected instance types
    expected_types = instance_types.split(",")
    for i, instance_type in enumerate(expected_types):
        assert overrides[i]["InstanceType"] == instance_type


def test_add_cluster_alarms_creates_required_alarms(
    mock_aws, cluster_template_generator
):
    """
    Test that _add_cluster_alarms creates the required CloudWatch alarms for the cluster.
    Verifies:
    1. High CPU alarm
    2. High Memory alarm
    3. High Memory Reservation alarm with user notification
    """
    # Setup notification SNS ARN parameter for alarms
    notification_sns_arn_parameter = Parameter(
        "NotificationSnsArn",
        Description="",
        Type="String",
        Default=cluster_template_generator.notifications_arn,
    )
    cluster_template_generator.notification_sns_arn = notification_sns_arn_parameter

    # Create a cluster to pass to _add_cluster_alarms
    cluster = Cluster("Cluster", ClusterName=Ref("AWS::StackName"))

    # Call the method under test
    cluster_template_generator._add_cluster_alarms(cluster)

    # Get the template resources
    template_dict = cluster_template_generator.template.to_dict()
    resources = template_dict["Resources"]

    # Verify CPU Alarm
    assert "ClusterHighCPUAlarm" in resources
    cpu_alarm = resources["ClusterHighCPUAlarm"]
    assert cpu_alarm["Type"] == "AWS::CloudWatch::Alarm"
    assert cpu_alarm["Properties"]["MetricName"] == "CPUUtilization"
    assert cpu_alarm["Properties"]["Namespace"] == "AWS/ECS"
    assert cpu_alarm["Properties"]["ComparisonOperator"] == "GreaterThanThreshold"
    assert cpu_alarm["Properties"]["Threshold"] == "60"
    assert cpu_alarm["Properties"]["EvaluationPeriods"] == 1
    assert cpu_alarm["Properties"]["Period"] == 300
    assert cpu_alarm["Properties"]["Statistic"] == "Average"

    # Check dimensions refer to the cluster
    dimensions = cpu_alarm["Properties"]["Dimensions"]
    assert len(dimensions) == 1
    assert dimensions[0]["Name"] == "ClusterName"
    assert dimensions[0]["Value"]["Ref"] == cluster.title

    # Verify alarm actions
    assert cpu_alarm["Properties"]["AlarmActions"] == [{"Ref": "NotificationSnsArn"}]

    # Verify Memory Alarm
    assert "ClusterHighMemoryAlarm" in resources
    memory_alarm = resources["ClusterHighMemoryAlarm"]
    assert memory_alarm["Type"] == "AWS::CloudWatch::Alarm"
    assert memory_alarm["Properties"]["MetricName"] == "MemoryUtilization"
    assert memory_alarm["Properties"]["Threshold"] == "60"

    # Verify Memory Reservation Alarm for user notification
    assert "ClusterHighMemoryReservationUserNotifcationAlarm" in resources
    reservation_alarm = resources["ClusterHighMemoryReservationUserNotifcationAlarm"]
    assert reservation_alarm["Type"] == "AWS::CloudWatch::Alarm"
    assert reservation_alarm["Properties"]["MetricName"] == "MemoryReservation"
    assert reservation_alarm["Properties"]["Threshold"] == "75"
    assert reservation_alarm["Properties"]["EvaluationPeriods"] == 3

    # Check that this alarm has both AlarmActions and OKActions
    assert reservation_alarm["Properties"]["AlarmActions"] == [
        {"Ref": "NotificationSnsArn"}
    ]
    assert reservation_alarm["Properties"]["OKActions"] == [
        {"Ref": "NotificationSnsArn"}
    ]

    # Verify alarm description indicates its purpose
    assert (
        "memory reservation is over 75%"
        in reservation_alarm["Properties"]["AlarmDescription"]
    )


def test_add_cluster_alarms_dimensions(mock_aws, cluster_template_generator):
    """
    Test that the dimensions in the CloudWatch alarms are correctly set
    to point to the ECS cluster.
    """
    # Setup notification SNS ARN parameter for alarms
    notification_sns_arn_parameter = Parameter(
        "NotificationSnsArn",
        Description="",
        Type="String",
        Default=cluster_template_generator.notifications_arn,
    )
    cluster_template_generator.notification_sns_arn = notification_sns_arn_parameter

    # Create a cluster with a custom name to verify dimension reference
    custom_cluster_name = "TestCluster"
    cluster = Cluster("CustomCluster", ClusterName=custom_cluster_name)

    # Call the method under test
    cluster_template_generator._add_cluster_alarms(cluster)

    # Get the template resources
    template_dict = cluster_template_generator.template.to_dict()
    resources = template_dict["Resources"]

    # Check all three alarms for correct dimensions
    alarm_names = [
        "ClusterHighCPUAlarm",
        "ClusterHighMemoryAlarm",
        "ClusterHighMemoryReservationUserNotifcationAlarm",
    ]

    for alarm_name in alarm_names:
        assert alarm_name in resources
        alarm = resources[alarm_name]
        dimensions = alarm["Properties"]["Dimensions"]

        assert len(dimensions) == 1
        assert dimensions[0]["Name"] == "ClusterName"
        assert dimensions[0]["Value"]["Ref"] == cluster.title


def test_add_cluster_alarms_instance_reference(mock_aws, cluster_template_generator):
    """
    Test that the instance of ClusterHighMemoryReservationUserNotifcationAlarm
    is saved to the generator object for later reference.
    """
    # Setup notification SNS ARN parameter for alarms
    notification_sns_arn_parameter = Parameter(
        "NotificationSnsArn",
        Description="",
        Type="String",
        Default=cluster_template_generator.notifications_arn,
    )
    cluster_template_generator.notification_sns_arn = notification_sns_arn_parameter

    # Create a cluster
    cluster = Cluster("Cluster", ClusterName=Ref("AWS::StackName"))

    # Call the method under test
    cluster_template_generator._add_cluster_alarms(cluster)

    # Verify that the alarm instance is saved to the generator
    assert hasattr(
        cluster_template_generator,
        "cluster_high_memory_reservation_user_notification_alarm",
    )
    assert (
        cluster_template_generator.cluster_high_memory_reservation_user_notification_alarm.title
        == "ClusterHighMemoryReservationUserNotifcationAlarm"
    )


def test_add_cluster_alarms_with_custom_notification_arn(
    mock_aws, cluster_template_generator
):
    """
    Test that _add_cluster_alarms uses the provided notification ARN
    for alarm actions.
    """
    # Setup custom notification SNS ARN parameter
    custom_notification_arn = "arn:aws:sns:us-east-1:123456789012:custom-topic"
    notification_sns_arn_parameter = Parameter(
        "NotificationSnsArn",
        Description="",
        Type="String",
        Default=custom_notification_arn,
    )
    cluster_template_generator.notification_sns_arn = notification_sns_arn_parameter

    # Create a cluster
    cluster = Cluster("Cluster", ClusterName=Ref("AWS::StackName"))

    # Call the method under test
    cluster_template_generator._add_cluster_alarms(cluster)

    # Get the template resources
    template_dict = cluster_template_generator.template.to_dict()
    resources = template_dict["Resources"]

    # Verify alarm actions point to the custom ARN
    for alarm_name in [
        "ClusterHighCPUAlarm",
        "ClusterHighMemoryAlarm",
        "ClusterHighMemoryReservationUserNotifcationAlarm",
    ]:
        assert alarm_name in resources
        assert resources[alarm_name]["Properties"]["AlarmActions"] == [
            {"Ref": "NotificationSnsArn"}
        ]


def test_add_cluster_and_alarms_integration(
    mock_aws, mock_ssm_ami_id_parameters, basic_cluster_config, monkeypatch
):
    """
    Integration test for _add_cluster method that verifies it correctly calls _add_cluster_alarms.
    Tests that alarms are created when calling the higher-level _add_cluster method.
    """
    # Mock the necessary environment functions
    monkeypatch.setattr(
        "cloudlift.config.get_region_for_environment", lambda env_name: TEST_REGION
    )
    monkeypatch.setattr(
        "cloudlift.config.get_ssl_certification_for_environment",
        lambda env_name: TEST_ACM_ARN,
    )
    monkeypatch.setattr(
        "cloudlift.config.get_notifications_arn_for_environment",
        lambda env_name: TEST_NOTIFICATIONS_ARN,
    )

    # Create a generator with basic configuration
    generator = ClusterTemplateGenerator(TEST_ENV_NAME, basic_cluster_config)

    # Set up the necessary prerequisites
    generator._create_vpc("10.0.0.0/16")
    generator._create_public_network(
        {
            "subnet-1": {"cidr": "10.0.1.0/24"},
            "subnet-2": {"cidr": "10.0.2.0/24"},
        }
    )
    generator._create_private_network(
        {
            "subnet-1": {"cidr": "10.0.3.0/24"},
            "subnet-2": {"cidr": "10.0.4.0/24"},
        },
        "eipalloc-12345678",
    )

    # Add necessary parameters
    generator._add_cluster_parameters()

    # Call _add_cluster which internally calls _add_cluster_alarms
    cluster = generator._add_cluster()

    # Verify the cluster was created
    template_dict = generator.template.to_dict()
    resources = template_dict["Resources"]
    assert "Cluster" in resources

    # Verify all three alarms were created
    assert "ClusterHighCPUAlarm" in resources
    assert "ClusterHighMemoryAlarm" in resources
    assert "ClusterHighMemoryReservationUserNotifcationAlarm" in resources


def test_add_cluster_alarms_thresholds(mock_aws, cluster_template_generator):
    """
    Test that the alarm thresholds are correctly set for each type of alarm.
    """
    # Setup notification SNS ARN parameter for alarms
    notification_sns_arn_parameter = Parameter(
        "NotificationSnsArn",
        Description="",
        Type="String",
        Default=cluster_template_generator.notifications_arn,
    )
    cluster_template_generator.notification_sns_arn = notification_sns_arn_parameter

    # Create a cluster
    cluster = Cluster("Cluster", ClusterName=Ref("AWS::StackName"))

    # Call the method under test
    cluster_template_generator._add_cluster_alarms(cluster)

    # Get the template resources
    template_dict = cluster_template_generator.template.to_dict()
    resources = template_dict["Resources"]

    # Verify CPU and Memory thresholds are set to 60%
    assert resources["ClusterHighCPUAlarm"]["Properties"]["Threshold"] == "60"
    assert resources["ClusterHighMemoryAlarm"]["Properties"]["Threshold"] == "60"

    # Verify Memory Reservation threshold is set to 75%
    assert (
        resources["ClusterHighMemoryReservationUserNotifcationAlarm"]["Properties"][
            "Threshold"
        ]
        == "75"
    )

    # Verify evaluation periods
    assert resources["ClusterHighCPUAlarm"]["Properties"]["EvaluationPeriods"] == 1
    assert resources["ClusterHighMemoryAlarm"]["Properties"]["EvaluationPeriods"] == 1
    assert (
        resources["ClusterHighMemoryReservationUserNotifcationAlarm"]["Properties"][
            "EvaluationPeriods"
        ]
        == 3
    )


@pytest.fixture
def cluster_type_config(request):
    """
    Fixture for cluster type configuration based on the parametrized test cases.
    """
    return request.param


def test_add_cluster_albs(cluster_template_generator, mock_aws):
    """
    Test that _add_cluster_albs creates ALBs and listeners with the correct configuration.
    Verifies:
    - One internal and one public ALB are created with proper scheme settings
    - ALB security groups are created correctly
    - HTTP and HTTPS listeners are created for each ALB
    - HTTP listeners have appropriate default actions based on ALB scheme
    - HTTPS listeners have the correct SSL policy and certificate
    """
    # Setup vpc and subnets required for ALB creation
    cluster_template_generator._create_vpc("10.0.0.0/16")
    cluster_template_generator._create_public_network({
        "subnet-1": {"cidr": "10.0.1.0/24"},
        "subnet-2": {"cidr": "10.0.2.0/24"},
    })
    cluster_template_generator._create_private_network({
        "subnet-1": {"cidr": "10.0.3.0/24"},
        "subnet-2": {"cidr": "10.0.4.0/24"},
    }, "eipalloc-12345678")
    
    # Create security group for EC2 hosts that ALB security groups will reference
    cluster_template_generator.sg_hosts = SecurityGroup(
        "SecurityGroupEc2Hosts",
        VpcId=Ref(cluster_template_generator.vpc),
        GroupDescription="SecurityGroupEc2Hosts"
    )
    cluster_template_generator.template.add_resource(cluster_template_generator.sg_hosts)
    
    # Call the method under test
    cluster_template_generator._add_cluster_albs()
    
    # Get the template resources
    template_dict = cluster_template_generator.template.to_dict()
    resources = template_dict["Resources"]
    
    # Verify ALBs were created
    alb_resources = [r for r in resources.values() if r["Type"] == "AWS::ElasticLoadBalancingV2::LoadBalancer"]
    assert len(alb_resources) == 2
    
    # Find internal and public ALBs
    internal_alb = next((alb for alb in alb_resources if alb["Properties"]["Scheme"] == "internal"), None)
    public_alb = next((alb for alb in alb_resources if alb["Properties"]["Scheme"] == "internet-facing"), None)
    
    assert internal_alb is not None, "Internal ALB was not created"
    assert public_alb is not None, "Public ALB was not created"
    
    # Verify ALB security groups were created
    security_groups = [r for r in resources.values() if r["Type"] == "AWS::EC2::SecurityGroup"]
    assert len(security_groups) >= 3  # At least 2 ALB SGs + 1 host SG
    
    # Verify ALB security group ingress rules
    sg_ingress_rules = [r for r in resources.values() if r["Type"] == "AWS::EC2::SecurityGroupIngress"]
    assert len(sg_ingress_rules) >= 2  # At least 2 ingress rules from ALB to hosts
    
    # Verify listeners were created
    listeners = [r for r in resources.values() if r["Type"] == "AWS::ElasticLoadBalancingV2::Listener"]
    assert len(listeners) == 4  # 2 HTTP + 2 HTTPS listeners
    
    # Count HTTP and HTTPS listeners
    http_listeners = [l for l in listeners if l["Properties"]["Protocol"] == "HTTP"]
    https_listeners = [l for l in listeners if l["Properties"]["Protocol"] == "HTTPS"]
    
    assert len(http_listeners) == 2
    assert len(https_listeners) == 2
    
    # Verify internal HTTP listener has a fixed response action
    internal_http = next((l for l in http_listeners if 
        l["Properties"]["LoadBalancerArn"]["Ref"] == internal_alb["Properties"]["Name"]), None)
    assert internal_http is not None
    assert internal_http["Properties"]["DefaultActions"][0]["Type"] == "fixed-response"
    assert internal_http["Properties"]["DefaultActions"][0]["FixedResponseConfig"]["StatusCode"] == "404"
    
    # Verify public HTTP listener has a redirect action
    public_http = next((l for l in http_listeners if 
        l["Properties"]["LoadBalancerArn"]["Ref"] == public_alb["Properties"]["Name"]), None)
    assert public_http is not None
    assert public_http["Properties"]["DefaultActions"][0]["Type"] == "redirect"
    assert public_http["Properties"]["DefaultActions"][0]["RedirectConfig"]["StatusCode"] == "HTTP_301"
    
    # Verify HTTPS listeners have correct SSL policy and certificate
    for https in https_listeners:
        assert https["Properties"]["SslPolicy"] == "ELBSecurityPolicy-FS-1-2-Res-2019-08"
        assert https["Properties"]["Certificates"][0]["CertificateArn"] == TEST_ACM_ARN
        assert https["Properties"]["DefaultActions"][0]["Type"] == "fixed-response"
        assert https["Properties"]["DefaultActions"][0]["FixedResponseConfig"]["StatusCode"] == "404"
    
    # Verify outputs were created for ALBs and listeners
    outputs = template_dict["Outputs"]
    alb_outputs = [o for o in outputs.keys() if "ALB" in o]
    listener_outputs = [o for o in outputs.keys() if "Listener" in o]
    
    assert len(alb_outputs) >= 2  # At least 2 ALB outputs
    assert len(listener_outputs) >= 4  # At least 4 listener outputs

@pytest.mark.parametrize("alb_scheme", [
    "internal", 
    "public"
])
def test_create_alb(cluster_template_generator, mock_aws, alb_scheme):
    """
    Test the _create_alb method with different ALB schemes.
    Verifies:
    - ALB is created with correct scheme
    - Correct subnets are used based on scheme
    - Security groups are properly configured
    - Tags are set correctly
    - Output is created for the ALB ARN
    """
    # Setup required resources
    cluster_template_generator._create_vpc("10.0.0.0/16")
    cluster_template_generator._create_public_network({
        "subnet-1": {"cidr": "10.0.1.0/24"},
        "subnet-2": {"cidr": "10.0.2.0/24"},
    })
    cluster_template_generator._create_private_network({
        "subnet-1": {"cidr": "10.0.3.0/24"},
        "subnet-2": {"cidr": "10.0.4.0/24"},
    }, "eipalloc-12345678")
    
    # Create security group for EC2 hosts that ALB security groups will reference
    cluster_template_generator.sg_hosts = SecurityGroup(
        "SecurityGroupEc2Hosts",
        VpcId=Ref(cluster_template_generator.vpc),
        GroupDescription="SecurityGroupEc2Hosts"
    )
    cluster_template_generator.template.add_resource(cluster_template_generator.sg_hosts)
    
    # Call the method under test
    alb = cluster_template_generator._create_alb(alb_scheme, 1)
    
    # Get the template resources
    template_dict = cluster_template_generator.template.to_dict()
    resources = template_dict["Resources"]
    
    # Find the ALB resource
    alb_name = generate_pascalcase_name(f"{alb_scheme}_ALB_1_{TEST_ENV_NAME}")
    assert alb_name in resources
    
    alb_resource = resources[alb_name]
    assert alb_resource["Type"] == "AWS::ElasticLoadBalancingV2::LoadBalancer"
    assert alb_resource["Properties"]["Name"] == alb_name
    assert alb_resource["Properties"]["Type"] == "application"
    assert alb_resource["Properties"]["Scheme"] == "internal" if alb_scheme == "internal" else "internet-facing"
    
    # Verify correct subnets are used based on scheme
    if alb_scheme == "internal":
        # For internal ALB, private subnets should be used
        subnet_refs = [subnet_ref["Ref"] for subnet_ref in alb_resource["Properties"]["Subnets"]]
        for subnet in cluster_template_generator.private_subnets:
            assert subnet.title in subnet_refs
    else:
        # For public ALB, public subnets should be used
        subnet_refs = [subnet_ref["Ref"] for subnet_ref in alb_resource["Properties"]["Subnets"]]
        for subnet in cluster_template_generator.public_subnets:
            assert subnet.title in subnet_refs
    
    # Verify security groups
    sg_name = generate_pascalcase_name(f"SG_{alb_name}")
    assert sg_name in resources
    
    # Verify ALB references the security group
    assert resources[sg_name]["Type"] == "AWS::EC2::SecurityGroup"
    assert alb_resource["Properties"]["SecurityGroups"][0]["Ref"] == sg_name
    
    # Verify tags
    expected_tags = [
        {"Key": "Name", "Value": alb_name},
        {"Key": "environment", "Value": TEST_ENV_NAME},
        {"Key": "Team", "Value": cluster_template_generator.team_name}
    ]
    for tag in expected_tags:
        assert tag in alb_resource["Properties"]["Tags"]
    
    # Verify output was created
    outputs = template_dict["Outputs"]
    output_title = generate_pascalcase_name(f"{alb_scheme}_ALB_1_ARN")
    assert output_title in outputs

@pytest.mark.parametrize("alb_scheme", [
    "internal", 
    "public"
])
def test_create_security_group(cluster_template_generator, mock_aws, alb_scheme):
    """
    Test the _create_security_group method with different ALB schemes.
    Verifies:
    - Security group is created with proper name and description
    - Ingress rules are added for HTTP and HTTPS traffic
    - Egress rule allows all outbound traffic
    - Ingress rule is created to allow traffic from ALB to EC2 hosts
    - Output is created for the security group ID
    """
    # Setup required resources
    cluster_template_generator._create_vpc("10.0.0.0/16")
    
    # Create security group for EC2 hosts that ALB security groups will reference
    cluster_template_generator.sg_hosts = SecurityGroup(
        "SecurityGroupEc2Hosts",
        VpcId=Ref(cluster_template_generator.vpc),
        GroupDescription="SecurityGroupEc2Hosts"
    )
    cluster_template_generator.template.add_resource(cluster_template_generator.sg_hosts)
    
    # Call the method under test
    alb_name = generate_pascalcase_name(f"{alb_scheme}_ALB_1_{TEST_ENV_NAME}")
    sg = cluster_template_generator._create_security_group(alb_name, alb_scheme, 1)
    
    # Get the template resources
    template_dict = cluster_template_generator.template.to_dict()
    resources = template_dict["Resources"]
    
    # Find the security group resource
    sg_name = generate_pascalcase_name(f"SG_{alb_name}")
    assert sg_name in resources
    
    sg_resource = resources[sg_name]
    assert sg_resource["Type"] == "AWS::EC2::SecurityGroup"
    assert sg_resource["Properties"]["GroupName"] == sg_name
    assert f"Security group for {alb_name}" in sg_resource["Properties"]["GroupDescription"]
    assert sg_resource["Properties"]["VpcId"]["Ref"] == cluster_template_generator.vpc.title
    
    # Verify ingress rules
    ingress_rules = sg_resource["Properties"]["SecurityGroupIngress"]
    assert len(ingress_rules) == 2  # HTTP and HTTPS
    
    http_rule = next((rule for rule in ingress_rules if rule["FromPort"] == "80"), None)
    https_rule = next((rule for rule in ingress_rules if rule["FromPort"] == "443"), None)
    
    assert http_rule is not None
    assert https_rule is not None
    
    for rule in [http_rule, https_rule]:
        assert rule["IpProtocol"] == "tcp"
        assert rule["CidrIp"] == "0.0.0.0/0"
    
    # Verify egress rule
    egress_rules = sg_resource["Properties"]["SecurityGroupEgress"]
    assert len(egress_rules) == 1
    assert egress_rules[0]["IpProtocol"] == "-1"
    assert egress_rules[0]["CidrIp"] == "0.0.0.0/0"
    
    # Verify ingress rule from ALB to EC2 hosts
    ingress_rule_name = generate_pascalcase_name(f"{alb_name}_To_EC2Hosts_Ingress", 64)
    assert ingress_rule_name in resources
    
    ingress_rule = resources[ingress_rule_name]
    assert ingress_rule["Type"] == "AWS::EC2::SecurityGroupIngress"
    assert ingress_rule["Properties"]["SourceSecurityGroupId"]["Ref"] == sg_name
    assert ingress_rule["Properties"]["GroupId"]["Ref"] == "SecurityGroupEc2Hosts"
    assert ingress_rule["Properties"]["IpProtocol"] == "-1"
    
    # Verify output was created
    outputs = template_dict["Outputs"]
    output_title = generate_pascalcase_name(f"SG_{alb_scheme}_1_ID")
    assert output_title in outputs

@pytest.mark.parametrize("alb_scheme", [
    "internal", 
    "public"
])
def test_create_alb_listeners(cluster_template_generator, mock_aws, alb_scheme):
    """
    Test the _create_alb_listeners method with different ALB schemes.
    Verifies:
    - HTTP and HTTPS listeners are created
    - Internal ALB HTTP listener has fixed response action
    - Public ALB HTTP listener has redirect action
    - All HTTPS listeners have fixed response actions
    - HTTPS listeners have correct SSL policy and certificate
    - Outputs are created for the listener ARNs
    """
    # Setup required resources
    cluster_template_generator._create_vpc("10.0.0.0/16")
    
    # Create ALB for listeners
    alb_name = generate_pascalcase_name(f"{alb_scheme}_ALB_1_{TEST_ENV_NAME}")
    alb = ALBLoadBalancer(
        title=alb_name,
        Name=alb_name,
        Type="application",
        Scheme="internal" if alb_scheme == "internal" else "internet-facing",
        Subnets=["subnet-1", "subnet-2"],  # Dummy values
        SecurityGroups=["sg-1"],  # Dummy value
    )
    cluster_template_generator.template.add_resource(alb)
    
    # Call the method under test
    listeners = cluster_template_generator._create_alb_listeners(alb, alb_scheme, 1)
    
    # Get the template resources
    template_dict = cluster_template_generator.template.to_dict()
    resources = template_dict["Resources"]
    
    # Verify two listeners were created and returned
    assert len(listeners) == 2
    
    # Find HTTP and HTTPS listeners
    http_listener_name = f"Http{generate_pascalcase_name(f'Listener_{alb_scheme}_1_{TEST_ENV_NAME}')}"
    https_listener_name = f"Https{generate_pascalcase_name(f'Listener_{alb_scheme}_1_{TEST_ENV_NAME}')}"
    
    assert http_listener_name in resources
    assert https_listener_name in resources
    
    http_listener = resources[http_listener_name]
    https_listener = resources[https_listener_name]
    
    # Verify common listener properties
    for listener in [http_listener, https_listener]:
        assert listener["Type"] == "AWS::ElasticLoadBalancingV2::Listener"
        assert listener["Properties"]["LoadBalancerArn"]["Ref"] == alb_name
    
    # Verify HTTP listener ports and protocols
    assert http_listener["Properties"]["Port"] == 80
    assert http_listener["Properties"]["Protocol"] == "HTTP"
    
    # Verify HTTPS listener ports and protocols
    assert https_listener["Properties"]["Port"] == 443
    assert https_listener["Properties"]["Protocol"] == "HTTPS"
    
    # Verify HTTP listener default actions based on ALB scheme
    if alb_scheme == "internal":
        assert http_listener["Properties"]["DefaultActions"][0]["Type"] == "fixed-response"
        assert http_listener["Properties"]["DefaultActions"][0]["FixedResponseConfig"]["StatusCode"] == "404"
    else:
        assert http_listener["Properties"]["DefaultActions"][0]["Type"] == "redirect"
        assert http_listener["Properties"]["DefaultActions"][0]["RedirectConfig"]["StatusCode"] == "HTTP_301"
        assert http_listener["Properties"]["DefaultActions"][0]["RedirectConfig"]["Protocol"] == "HTTPS"
        assert http_listener["Properties"]["DefaultActions"][0]["RedirectConfig"]["Port"] == "443"
    
    # Verify HTTPS listener SSL configuration
    assert https_listener["Properties"]["SslPolicy"] == "ELBSecurityPolicy-FS-1-2-Res-2019-08"
    assert len(https_listener["Properties"]["Certificates"]) == 1
    assert https_listener["Properties"]["Certificates"][0]["CertificateArn"] == TEST_ACM_ARN
    
    # Verify HTTPS listener default actions
    assert https_listener["Properties"]["DefaultActions"][0]["Type"] == "fixed-response"
    assert https_listener["Properties"]["DefaultActions"][0]["FixedResponseConfig"]["StatusCode"] == "404"
    
    # Verify outputs were created
    outputs = template_dict["Outputs"]
    http_output_title = generate_pascalcase_name(f"Listener_HTTP_{alb_scheme}_1_ARN")
    https_output_title = generate_pascalcase_name(f"Listener_HTTPS_{alb_scheme}_1_ARN")
    
    assert http_output_title in outputs
    assert https_output_title in outputs

def test_fixed_response_action(cluster_template_generator):
    """Test _create_fixed_response_action method creates correct action configuration"""
    action = cluster_template_generator._create_fixed_response_action()
    
    assert action.__class__.__name__ == "Action"
    assert action.Type == "fixed-response"
    assert action.FixedResponseConfig.__class__.__name__ == "FixedResponseConfig"
    assert action.FixedResponseConfig.ContentType == "text/plain"
    assert action.FixedResponseConfig.StatusCode == "404"
    assert action.FixedResponseConfig.MessageBody == "No matching host found"

def test_redirect_action(cluster_template_generator):
    """Test _create_redirect_action method creates correct action configuration"""
    action = cluster_template_generator._create_redirect_action()
    
    assert action.__class__.__name__ == "Action"
    assert action.Type == "redirect"
    assert action.RedirectConfig.__class__.__name__ == "RedirectConfig"
    assert action.RedirectConfig.StatusCode == "HTTP_301"
    assert action.RedirectConfig.Protocol == "HTTPS"
    assert action.RedirectConfig.Port == "443"

def test_add_cluster_albs_with_ssl_certificate(cluster_template_generator, mock_aws):
    """
    Test _add_cluster_albs uses the correct SSL certificate from the environment.
    """
    # Setup vpc and subnets required for ALB creation
    cluster_template_generator._create_vpc("10.0.0.0/16")
    cluster_template_generator._create_public_network({
        "subnet-1": {"cidr": "10.0.1.0/24"},
        "subnet-2": {"cidr": "10.0.2.0/24"},
    })
    cluster_template_generator._create_private_network({
        "subnet-1": {"cidr": "10.0.3.0/24"},
        "subnet-2": {"cidr": "10.0.4.0/24"},
    }, "eipalloc-12345678")
    
    # Create security group for EC2 hosts
    cluster_template_generator.sg_hosts = SecurityGroup(
        "SecurityGroupEc2Hosts",
        VpcId=Ref(cluster_template_generator.vpc),
        GroupDescription="SecurityGroupEc2Hosts"
    )
    cluster_template_generator.template.add_resource(cluster_template_generator.sg_hosts)
    
    # Call the method under test
    cluster_template_generator._add_cluster_albs()
    
    # Get the template resources
    template_dict = cluster_template_generator.template.to_dict()
    resources = template_dict["Resources"]
    
    # Find all HTTPS listeners
    https_listeners = [r for r in resources.values() if 
                     r["Type"] == "AWS::ElasticLoadBalancingV2::Listener" and
                     r["Properties"]["Protocol"] == "HTTPS"]
                     
    assert len(https_listeners) == 2
    
    # Verify all HTTPS listeners use the correct certificate
    for listener in https_listeners:
        certificates = listener["Properties"]["Certificates"]
        assert len(certificates) == 1
        assert certificates[0]["CertificateArn"] == TEST_ACM_ARN
