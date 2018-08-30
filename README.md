# GP-Wrapper

GP-Wrapper is the front-end for [GPyTorch](https://github.com/cornellius-gp/gpytorch/). It abstracts away the training loop, 
making a lot of boilerplate code obsolete. A simple GP.fit(X, y) is enough. 

We use [skorch](https://skorch.readthedocs.io/en/latest/) as inspiration, and adapted its code for our front-end design.

# Four steps to work with our GPyTorch Wrapper:

<b>1. Define a GP model </b>

<b>2. wrap the model into one of the following GPwrappers:</b>

    - ExactGaussianProcess (Use criterion = gpytorch.mlls.ExactMarginalLogLikelihood as default)
    
         - ExactGaussianProcessRegressor  (Additionally, use likelihood = GaussianLikelihood as default)
         
    - VariationalGaussianProcess (Use criterion = gpytorch.mlls.VariationalMarginalLogLikelihood as default)
    
         - VariationalGaussianProcessRegressor (Additionally, use likelihood = GaussianLikelihood as default)
         
         - VariationalGaussianProcessClassifier (Additionally, use likelihood = BernoulliLikelihood as default)
         
<b>3. fit(x_train, y_train):</b>  # Find optimal model hyperparameters with default optimizer torch.optim.Adam

<b>4. predict_proba(x_test):</b>  # Return a GaussianRandomVariable as the predictive outputs for x_test


# Which notebooks to read

If you're just starting work with Gaussian processes, check out the simple [regression](examples/Quick_Start_Simple_GP_Regression.ipynb) and
[classification](examples/Simple_GP_Classification.ipynb). These show the most basic usage of GPyTorch and provide links to
useful reading material.

If you have a specific task in mind, then check out this [flowchart](flowchart.pdf) to find which notebook will help you.

Here's a verbal summary of the flow chart:

## Regression

*Do you have lots of data?*

**No:** Start with the [basic example](examples/Quick_Start_Simple_GP_Regression.ipynb)

*Is your training data one-dimensional?*

**Yes:** Use [KissGP regression](examples/Kissgp_GP_Regression.ipynb)

*Does your output decompose additively?*

**Yes:** Use [Additive Grid Interpolation](examples/Kissgp_Additive_Regression.ipynb)

*Is your trainng data three-dimensional or less?*

**Yes**: Exploit [Kronecker structure](examples/Kissgp_Kronecker_Product_Regression.ipynb)

**No**: Try Deep Kernel regression (example pending)

### Variational Regression (new!)

Try this if:
- You have too much data for exact inference, even with KissGP/Deep kernel learning/etc.
- Your model will need variational inference anyways (e.g. if you're doing some sort of clustering)

See [the example](examples/Kissgp_Variational_Regression.ipynb) for more info.

###  Multitask Regression

See [the example](examples/Multitask_GP_Regression.ipynb) for more info.


## Classification

*Do you have lots of data?*

**No:** Start with the [basic example](examples/Simple_GP_Classification.ipynb)

*Is your training data one-dimensional?*

**Yes:** Use [KissGP classification](examples/Kissgp_GP_Classification.ipynb)

*Does your output decompose additively?*

**Yes:** Use [Additive Grid Interpolation](examples/Kissgp_Additive_Classification.ipynb)

*Is your training data three-dimensional or less?*

**Yes**: Exploit [Kronecker structure](examples/Kissgp_Kronecker_Product_Classification.ipynb)

**No**: Try Deep Kernel [classification](examples/DKL_MNIST.ipynb) (under construction)