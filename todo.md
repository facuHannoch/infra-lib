- Store state

Yes, that's exactly what Pulumi state is for. By default with `pulumi login --local`, state is stored in `~/.pulumi/stacks/`. Each stack gets a JSON file there with all the resource IDs, so Pulumi knows exactly what to destroy.

So `pulumi destroy` (or the library equivalent) on a named stack weeks later would tear down all the resources for that deployment — VM, NIC, NSG, VNet, everything.

The risk is: if you lose `~/.pulumi/stacks/`, you lose the ability to cleanly destroy through Pulumi. You'd have to go to the Azure portal and delete manually. This is the main reason people use Pulumi Cloud — the state is stored remotely and survives machine changes.

For this library, worth thinking about: should state live in `~/.pulumi/` (default, machine-local), or should we default to storing it somewhere more durable — like an Azure Blob Storage container (which we could provision automatically on first use)?

Azure Blob as state backend would mean:
- State survives if you switch machines
- Multiple people can share the same deployments
- Still no Pulumi Cloud account needed

It's a one-time setup cost but makes the library much more robust. What do you think?