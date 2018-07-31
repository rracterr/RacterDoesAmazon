#!/usr/bin/env python3

# Ben Knauss

import pprint
import argparse
import time
import boto3
from pick import pick


parser = argparse.ArgumentParser(
    description="Create VPC Peers between two accounts")

parser.add_argument(
    '--primary', '-p',
    help="The primary account to peer"
)

parser.add_argument(
    '--secondary', '-s',
    action='append',
    help="The primary account to peer"
)

parser.add_argument(
     '--verbose', '-v', 
     help='increase output verbosity',
     action="store_true"
)

args = parser.parse_args()


def get_account_alias(profile):
    session = boto3.Session(profile_name=profile)
    
    iam_client = session.client('iam')
    
    aliases = iam_client.list_account_aliases()['AccountAliases'][0]
    
    return aliases

    
def get_account_number(profile):
    session = boto3.Session(profile_name=profile)
    
    iam_client = session.client('iam')
    
    serole = iam_client.get_role(RoleName='Move-SE')['Role']
    
    arn_elements=serole['Arn'].split(':')
    
    return arn_elements[4]
    

def account_data_collect(profile):
    data={}
    options=[]

    print("-----------------------------------")

    session = boto3.Session(profile_name=profile)
    ec2_client = session.client('ec2')
    
    data['profile'] = profile
    data['region'] = session.region_name
    print("Collecting Data on Profile: {}  Region: {}".format(data['profile'], data['region']) )

    vpcs = ec2_client.describe_vpcs()['Vpcs']
    
    if len(vpcs) > 1:
        for vpc in vpcs:
            options.append("VPC ID: {}    CIDR: {}".format(vpc['VpcId'], vpc['CidrBlock']))

        title="Please pick the appropriate VPC in the {} account".format(profile)
        option, index = pick(options, title, indicator='=>', default_index=0)
        
        data['vpc_id'] = vpcs[index]['VpcId']
        data['vpc_cidr'] = vpcs[index]['CidrBlock']
    elif len(vpcs) == 1:
        data['vpc_id'] = vpcs[0]['VpcId']
        data['vpc_cidr'] = vpcs[0]['CidrBlock']
        print("Only a single VPC Defined in account")
    else:
        print("you have no VPCS to peer in this account")   

    print("Selected VPC ID: {}  CIDR {}".format(data['vpc_id'], data['vpc_cidr']))

    data['account_id'] = get_account_number(profile)
    data['account_alias'] = get_account_alias(profile)

    subnets = ec2_client.describe_subnets(Filters=[
            {'Name':'vpc-id', 'Values': [ data['vpc_id'] ]},
            {'Name':'tag:Name', 'Values': ['Private-*']}
        ])['Subnets']
    
    data['vpc_subnets']=[]

    for subnet in subnets:
       data['vpc_subnets'].append(subnet['SubnetId'])

    print("Subnets: {}".format(",".join(data['vpc_subnets']) ))

    data['vpc_routes'] = ec2_client.describe_route_tables(Filters=[
            {'Name':'route.origin', 'Values': ['CreateRoute']},
            {'Name':'route.state', 'Values': ['active']},
            {'Name':'association.subnet-id', 'Values': data['vpc_subnets']}
            ])['RouteTables']

    for route in data['vpc_routes']:
        print("Route Table ID: {}".format(route['RouteTableId']))

    return data

def process(primary, secondary):
    print("-----------------------------------")
    print("Processing {} -> {}".format(primary['profile'], secondary['profile']))

    primary_session = boto3.Session(profile_name=primary['profile'])
    ec2_client = primary_session.client('ec2')
    waiter = ec2_client.get_waiter('vpc_peering_connection_exists')

    # primary
    try:
       VpcPeeringConnectionId = ec2_client.create_vpc_peering_connection(VpcId=primary['vpc_id'], PeerVpcId=secondary['vpc_id'], PeerRegion=secondary['region'], PeerOwnerId=secondary['account_id'])['VpcPeeringConnection']['VpcPeeringConnectionId']
    except Exception as e:
       print(e)

    waiter.wait(VpcPeeringConnectionIds=[VpcPeeringConnectionId])
    time.sleep(4)

    print("1)ec2_client.create_vpc_peering_connection({}, {}, {}, {})".format(primary['vpc_id'], secondary['vpc_id'], secondary['region'], secondary['account_id']))
    print("2)VpcPeeringConnectionId: {}".format(VpcPeeringConnectionId))

    ec2_client.create_tags(Resources=[VpcPeeringConnectionId], Tags=[{'Key': 'Name', 'Value': "{}-{}".format(primary['account_alias'], secondary['account_alias'])}])
    
    for route in primary['vpc_routes']:
       try:
          ec2_client.create_route( RouteTableId=route['RouteTableId'], DestinationCidrBlock=secondary['vpc_cidr'], VpcPeeringConnectionId=VpcPeeringConnectionId)
       except Exception as e:
          if e.response['Error']['Code'] == 'RouteAlreadyExists':
             print("Primary Route Already Exists")

    secondary_session = boto3.Session(profile_name=secondary['profile'])
    ec2_client = secondary_session.client('ec2')

    # secondary
    try: 
       SecondaryVpcPeeringConnectionId = ec2_client.accept_vpc_peering_connection(VpcPeeringConnectionId=VpcPeeringConnectionId)['VpcPeeringConnection']['VpcPeeringConnectionId']
    except Exception as e:
       print(e)

    waiter.wait(VpcPeeringConnectionIds=[VpcPeeringConnectionId])
    time.sleep(10)

    print("3)ec2_client.accept_vpc_peering_connection({})".format(VpcPeeringConnectionId))
    print("4)SecondaryVpcPeeringConnectionId: {}".format(SecondaryVpcPeeringConnectionId))

    ec2_client.create_tags(Resources=[SecondaryVpcPeeringConnectionId], Tags=[{'Key': 'Name', 'Value': "{}-{}".format(secondary['account_alias'], primary['account_alias'])}])

    for route in secondary['vpc_routes']:
       try:
          ec2_client.create_route( RouteTableId=route['RouteTableId'], DestinationCidrBlock=primary['vpc_cidr'], VpcPeeringConnectionId=SecondaryVpcPeeringConnectionId)
       except Exception as e:
          if e.response['Error']['Code'] == 'RouteAlreadyExists':
             print("Secondary Route Already Exists")


def main():
    secondary_account_data = []

    print('create_vpc_peer.py - Benjamin Knauss')

    if not args.primary:
        print(" ")
        print("Please provide a primary AWS profile name")
        print(" ")
        parser.print_help()
        exit()

    if not args.secondary:
        print(" ")
        print("Please provide a secondary AWS profile name")
        print(" ")
        parser.print_help()
        exit()

    primary_account_data = account_data_collect(args.primary)
    
    for secondary_profile in args.secondary:
        secondary_account_data.append(account_data_collect(secondary_profile))

    for secondary in secondary_account_data:
        process(primary_account_data, secondary)

    print('Complete')


if __name__ == '__main__':
     main()
