import aws_cdk as core
import aws_cdk.assertions as assertions

from worldcup_platform.worldcup_platform_stack import WorldcupPlatformStack

# example tests. To run these tests, uncomment this file along with the example
# resource in worldcup_platform/worldcup_platform_stack.py
def test_sqs_queue_created():
    app = core.App()
    stack = WorldcupPlatformStack(app, "worldcup-platform")
    template = assertions.Template.from_stack(stack)

#     template.has_resource_properties("AWS::SQS::Queue", {
#         "VisibilityTimeout": 300
#     })
