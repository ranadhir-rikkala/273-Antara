#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"

tag() { echo "ResourceType=$1,Tags=[{Key=Name,Value=$ANTARA_NAME},{Key=project,Value=antara}]"; }

vpc=$(aws ec2 create-vpc --cidr-block "$VPC_CIDR" --tag-specifications "$(tag vpc)" --query Vpc.VpcId --output text)
aws ec2 modify-vpc-attribute --vpc-id "$vpc" --enable-dns-hostnames
subnet=$(aws ec2 create-subnet --vpc-id "$vpc" --cidr-block "$SUBNET_CIDR" --tag-specifications "$(tag subnet)" \
  --query Subnet.SubnetId --output text)
aws ec2 modify-subnet-attribute --subnet-id "$subnet" --map-public-ip-on-launch
igw=$(aws ec2 create-internet-gateway --tag-specifications "$(tag internet-gateway)" \
  --query InternetGateway.InternetGatewayId --output text)
aws ec2 attach-internet-gateway --vpc-id "$vpc" --internet-gateway-id "$igw"
rtb=$(aws ec2 describe-route-tables --filters "Name=vpc-id,Values=$vpc" --query 'RouteTables[0].RouteTableId' --output text)
aws ec2 create-route --route-table-id "$rtb" --destination-cidr-block 0.0.0.0/0 --gateway-id "$igw" >/dev/null

sg=$(aws ec2 create-security-group --group-name "$ANTARA_NAME-nodes" --description "Antara nodes" --vpc-id "$vpc" \
  --query GroupId --output text)
aws ec2 authorize-security-group-ingress --group-id "$sg" --protocol tcp --port 22 --cidr "$TEAM_CIDR" >/dev/null
aws ec2 authorize-security-group-ingress --group-id "$sg" --protocol -1 --cidr "$VPC_CIDR" >/dev/null

if ! aws ec2 describe-key-pairs --key-names "$KEY_NAME" >/dev/null 2>&1; then
  aws ec2 create-key-pair --key-name "$KEY_NAME" --query KeyMaterial --output text > "$KEY_NAME.pem"
  chmod 600 "$KEY_NAME.pem"
fi

ubuntu_ami=$(aws ssm get-parameter --name /aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id \
  --query Parameter.Value --output text)
gpu_ami=$(aws ssm get-parameter \
  --name /aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id \
  --query Parameter.Value --output text)

launch() {
  aws ec2 run-instances --image-id "$1" --instance-type "$2" --key-name "$KEY_NAME" --subnet-id "$subnet" \
    --security-group-ids "$sg" --block-device-mappings "DeviceName=/dev/sda1,Ebs={VolumeSize=$3,VolumeType=gp3}" \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$ANTARA_NAME-$4},{Key=project,Value=antara}]" \
    --query 'Instances[0].InstanceId' --output text
}

server=$(launch "$ubuntu_ami" "$SERVER_TYPE" 80 server)
gpu=$(launch "$gpu_ami" "$GPU_TYPE" 120 gpu)
aws ec2 wait instance-running --instance-ids "$server" "$gpu"

cat > "$STATE_FILE" <<STATE
VPC_ID=$vpc
SUBNET_ID=$subnet
IGW_ID=$igw
SG_ID=$sg
SERVER_ID=$server
GPU_ID=$gpu
STATE
aws ec2 describe-instances --instance-ids "$server" "$gpu" \
  --query 'Reservations[].Instances[].[Tags[?Key==`Name`]|[0].Value,PublicIpAddress,PrivateIpAddress]' --output table
