locals {
  tags = merge(var.tags, { "voxdesk:component" = "network", "voxdesk:name" = var.name })

  azs = slice(data.aws_availability_zones.available.names, 0, var.az_count)

  # /20 private, /20 public, carved out of the /16 in order, so the layout is
  # readable in the console and in `terraform state list`.
  public_subnets  = { for index, az in local.azs : az => cidrsubnet(var.cidr_block, 8, index) }
  private_subnets = { for index, az in local.azs : az => cidrsubnet(var.cidr_block, 8, index + 16) }

  nat_count = var.single_nat_gateway ? 1 : var.az_count
}

data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "this" {
  cidr_block           = var.cidr_block
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = merge(local.tags, { Name = "${var.name}-vpc" })
}

resource "aws_subnet" "public" {
  for_each = local.public_subnets

  vpc_id                  = aws_vpc.this.id
  cidr_block              = each.value
  availability_zone       = each.key
  map_public_ip_on_launch = false

  tags = merge(local.tags, { Name = "${var.name}-public-${each.key}", "kubernetes.io/role/elb" = "1" })
}

resource "aws_subnet" "private" {
  for_each = local.private_subnets

  vpc_id            = aws_vpc.this.id
  cidr_block        = each.value
  availability_zone = each.key

  tags = merge(local.tags, { Name = "${var.name}-private-${each.key}", "kubernetes.io/role/internal-elb" = "1" })
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id

  tags = merge(local.tags, { Name = "${var.name}-igw" })
}

resource "aws_eip" "nat" {
  count = local.nat_count

  domain = "vpc"

  tags = merge(local.tags, { Name = "${var.name}-nat-${count.index}" })
}

resource "aws_nat_gateway" "this" {
  count = local.nat_count

  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[local.azs[count.index]].id

  tags = merge(local.tags, { Name = "${var.name}-nat-${count.index}" })

  depends_on = [aws_internet_gateway.this]
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }

  tags = merge(local.tags, { Name = "${var.name}-public-rt" })
}

resource "aws_route_table_association" "public" {
  for_each = aws_subnet.public

  subnet_id      = each.value.id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  count = local.nat_count

  vpc_id = aws_vpc.this.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.this[count.index].id
  }

  tags = merge(local.tags, { Name = "${var.name}-private-rt-${count.index}" })
}

# With one NAT gateway every private subnet shares it; otherwise each subnet
# egresses through the gateway in its own AZ so an AZ loss is not an outage.
resource "aws_route_table_association" "private" {
  for_each = local.private_subnets

  subnet_id = aws_subnet.private[each.key].id
  route_table_id = (
    var.single_nat_gateway
    ? aws_route_table.private[0].id
    : aws_route_table.private[index(local.azs, each.key)].id
  )
}

# S3 and DynamoDB traffic never leaves AWS. This is the endpoint that carries
# brand-asset uploads, so it stays even though it looks like an optimisation.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${data.aws_region.current.name}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = concat(aws_route_table.private[*].id, [aws_route_table.public.id])

  tags = merge(local.tags, { Name = "${var.name}-s3" })
}

resource "aws_vpc_endpoint" "dynamodb" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${data.aws_region.current.name}.dynamodb"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = aws_route_table.private[*].id

  tags = merge(local.tags, { Name = "${var.name}-dynamodb" })
}

data "aws_region" "current" {}

resource "aws_cloudwatch_log_group" "flow" {
  name              = "/voxdesk/${var.name}/vpc-flow"
  retention_in_days = var.flow_log_retention_days

  tags = local.tags
}

data "aws_iam_policy_document" "flow_assume" {
  statement {
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["vpc-flow-logs.amazonaws.com"]
    }

    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "flow" {
  name               = "${var.name}-vpc-flow"
  assume_role_policy = data.aws_iam_policy_document.flow_assume.json

  tags = local.tags
}

data "aws_iam_policy_document" "flow_write" {
  statement {
    effect = "Allow"

    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogGroups",
      "logs:DescribeLogStreams",
    ]

    resources = ["${aws_cloudwatch_log_group.flow.arn}:*"]
  }
}

resource "aws_iam_role_policy" "flow" {
  name   = "${var.name}-vpc-flow"
  role   = aws_iam_role.flow.id
  policy = data.aws_iam_policy_document.flow_write.json
}

resource "aws_flow_log" "this" {
  vpc_id                   = aws_vpc.this.id
  traffic_type             = "REJECT"
  log_destination_type     = "cloud-watch-logs"
  log_destination          = aws_cloudwatch_log_group.flow.arn
  iam_role_arn             = aws_iam_role.flow.arn
  max_aggregation_interval = 600

  tags = local.tags
}
